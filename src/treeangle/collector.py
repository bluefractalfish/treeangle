"""
logic for determining which portions of the raster and what vector information is needed for export

given options, project; look at raster and find kite information. :

"""
from dataclasses import dataclass, asdict 
from datetime import datetime, timezone
from pathlib import Path
import csv 
import json
import math
import re
import shutil
import math
import random 

import numpy as np 
from osgeo import gdal 

from qgis.PyQt.QtCore import Qt, QObject, QPointF, QMetaType, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPen 
from qgis.PyQt.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
            QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTabWidget,
                    QVBoxLayout, QWidget, QProgressDialog
                    )
from qgis.core import(
        Qgis, QgsProject, QgsRasterLayer, QgsSettings, QgsVectorLayer, 
            QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsField, QgsGeometry, QgsPointXY, QgsSpatialIndex, QgsVectorFileWriter, )

from .geometry import Kite, Point
ROLES = ["base", "tip", "left", "right"]
SKELETON = [[1, 3], [3, 2], [2, 4], [4, 1]]

################ SAMPLE HELPERS ###################################################################

def pixel_to_world(point, transform):
    col, row = point
    x0, a, b, y0, d, e = transform
    return [x0 + a * col + b * row, y0 + d * col + e * row]


def world_to_pixel(point, transform):
    x0, a, b, y0, d, e = transform
    det = a * e - b * d
    if not math.isfinite(det) or det == 0:
        raise ValueError("determinate of transform dont work") 
    x, y = point[0] - x0, point[1] - y0
    return [(e * x - b * y) / det, (-d * x + a * y) / det]


def pixel_bbox(points):
    xs, ys = zip(*points)
    if not all(math.isfinite(v) for v in (*xs, *ys)):
        raise ValueError("non-finite coordinates. need to be finite")
    return [min(xs), min(ys), max(xs), max(ys)]


def crop_window(points,
                transform,
                raster_width,
                raster_height,
                fraction=0.15,
                minimum=16,
                fixed_size=None,
                square=False):

    box = pixel_bbox([world_to_pixel(p, transform) for p in points])
    xmin, ymin, xmax, ymax = box
    tolerance = 1e-6

    xmin, ymin = max(0, xmin), max(0, ymin)
    xmax, ymax = min(raster_width, xmax), min(raster_height, ymax)
    margin = math.ceil(max(minimum, fraction * max(xmax - xmin, ymax - ymin)))
    requested = [math.floor(xmin) - margin, math.floor(ymin) - margin,
                 math.ceil(xmax) + margin, math.ceil(ymax) + margin]

    if fixed_size is None and not square:
        left, top = max(0, requested[0]), max(0, requested[1])
        right, bottom = min(raster_width, requested[2]), min(raster_height, requested[3])
    else:
        needed = max(requested[2] - requested[0], requested[3] - requested[1])
        size = int(fixed_size) if fixed_size is not None else min(needed, raster_width, raster_height)
        left = max(0, min(raster_width - size, (requested[0] + requested[2] - size) // 2))
        top = max(0, min(raster_height - size, (requested[1] + requested[3] - size) // 2))
        right, bottom = left + size, top + size 

    return [left, top, right - left, bottom - top], (
        left > requested[0] or top > requested[1] or
        right < requested[2] or bottom < requested[3]
    )


def shifted_transform(transform, left, top):
    origin = pixel_to_world([left, top], transform)
    return [origin[0], transform[1], transform[2], origin[1], transform[4], transform[5]]


def bbox_ring(box):
    left, top, right, bottom = box
    return [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]


def pixel_label(points, polygon, transform, width, height):
    keypoints = [world_to_pixel(p, transform) for p in points]
    ring = [world_to_pixel(p, transform) for p in polygon]
    full_box = pixel_bbox(ring)
    clipped_box = [max(0.0, full_box[0]), max(0.0, full_box[1]),
                   min(float(width), full_box[2]), min(float(height), full_box[3])]
    tol = 1e-6 
    inside = lambda p: -tol <= p[0] <= width + tol and -tol <= p[1] <= height + tol
    return {"bbox_xyxy_px": clipped_box, "bbox_unclipped_xyxy_px": full_box,
            "polygon_px": ring, "keypoints_px_p0_p1_p2_p3": keypoints,
            "keypoints_inside_image": [inside(p) for p in keypoints],
            "truncated": not all(inside(p) for p in ring)}



def png_dimensions(width, height, maximum):
    scale = min(1.0, maximum / max(width, height)) if maximum else 1.0
    return max(1, round(width * scale)), max(1, round(height * scale))


def crown_order(points, canonicalize):
    """left is left while looking from base to tip (pixel y increases down)"""
    if not canonicalize:
        return [0, 1, 2, 3]
    (x0, y0), (x1, y1), left, right = points
    side = lambda p: (x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)
    return [0, 1, 2, 3] if side(left) < 0 else [0, 1, 3, 2]


def coco_annotation(label, image_id, annotation_id, native_size, png_size, canonicalize=True):
    """convert a TIFF-relative label to a PNG-relative COCO pose annotation"""
    width, height = native_size
    sx, sy = png_size[0] / width, png_size[1] / height
    points = label["keypoints_px_p0_p1_p2_p3"]
    order = crown_order(points, canonicalize)
    keypoints = []
    for i in order:
        x, y = points[i]
        if label["keypoints_inside_image"][i]:
            # Only floating-point tolerance is clamped; genuinely outside points stay missing.
            keypoints.extend([min(width, max(0, x)) * sx, min(height, max(0, y)) * sy, 2])
        else:
            keypoints.extend([0, 0, 0])
    x0, y0, x1, y1 = label["bbox_xyxy_px"]
    box = [x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy]
    if min(box[2:]) <= 0:
        raise ValueError("empty bounding box")
    return {"id": annotation_id, "image_id": image_id, "category_id": 1,
            "bbox": box, "area": box[2] * box[3], "iscrowd": 0,
            "keypoints": keypoints, "num_keypoints": sum(v > 0 for v in keypoints[2::3])}, order


def new_coco():
    """ ROLES determines the order of root, tip, left, right, root. Roboflow expects the same order """ 
    return {"info": {"description": "TreeAngle fallen-tree keypoints"}, "licenses": [],
            "images": [], "annotations": [],
            "categories": [{"id": 1, "name": "fallen_tree", "supercategory": "tree",
                            "keypoints": ROLES, "skeleton": SKELETON}]}


def split_groups(samples):
    """group crops sharing source pixels or a tree; never split a group for evaluation."""
    parents = list(range(len(samples)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(a, b):
        parents[root(b)] = root(a)

    seen, active = {}, []
    for i in sorted(range(len(samples)), key=lambda n: samples[n]["window"][0]):
        x, y, width, height = samples[i]["window"]
        # Samples left of this window can no longer overlap a future window.
        active = [j for j in active
                  if samples[j]["window"][0] + samples[j]["window"][2] > x]
        for j in active:
            _, other_y, _, other_height = samples[j]["window"]
            if y < other_y + other_height and other_y < y + height:
                join(i, j)
        active.append(i)

        # The same tree may appear as a neighbor in more than one crop.
        for link in samples[i].get("annotations", []):
            tree_id = link["tree_id"]
            if tree_id in seen:
                join(i, seen[tree_id])
            else:
                seen[tree_id] = i

    return [f"group_{root(i):06d}" for i in range(len(samples))]

def stretch_rgb(arrays, valid, ranges):
    import numpy as np
    channels = []
    for data, (low, high) in zip(arrays, ranges):
        scaled = (data.astype(np.float64) - low) * (255.0 / (high - low))
        scaled = np.where(valid & np.isfinite(scaled), scaled, 0)
        channels.append(np.rint(np.clip(scaled, 0, 255)).astype(np.uint8))
    return np.ascontiguousarray(np.stack(channels, axis=-1))


    

def is_export_source(layer):
    required = {"tree_id", *[f"p{i}_{a}" for i in range(4) for a in "xy"]}
    return (isinstance(layer, QgsVectorLayer) and layer.isValid()
            and layer.geometryType() == Qgis.GeometryType.Polygon
            and required.issubset(set(layer.fields().names()))
            and not {"ta_target", "ta_export"}.intersection(layer.fields().names()))

@dataclass
class Tree:
    tree_id: str
    key: str
    points: list
    ring: list
    geometry: object
    attributes: dict
    source_layer: str
    source_fid: int
    input_crs: str


def plain(value):
    """converts QGIS field values into ordinary JSON values."""
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, "toString"):
        return value.toString(Qt.DateFormat.ISODate)
    return str(value)


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def polygon(points):
    return QgsGeometry.fromPolygonXY([[QgsPointXY(*p) for p in points]])


def open_raster(raster): 
    """ use files actual crs and grid, rather than the map canvas """

    if not isinstance(raster, QgsRasterLayer) or not raster.isValid():
        raise ValueError("invalid raster selection")

    source = gdal.OpenEx(raster.source(), gdal.OF_RASTER | gdal.OF_READONLY)
    if source is None: 
        raise ValueError("gdal could not read raster. chose local tiff or vrt")
    
    transform = source.GetGeoTransform(can_return_null=True)
    crs = QgsCoordinateReferenceSystem()
    world_to_pixel(pixel_to_world([0, 0], transform), transform)
    return source, crs, list(transform)

def tree_from_feature(feature, layer, transform):
    """ take qgis feature and convert into Tree class """ 
    attrs = {name: plain(feature[name]) for name in layer.fields().names()}
    tree_id = str(attrs.get("tree_id") or "").strip()

    if not tree_id: 
        raise ValueError(f"missing tree_id in {layer.name()}, feature {feature.id()} ")
    points = [[float(attrs[f"p{i}_{a}"]) for a in "xy"] for i in range(4)]
    pixel_bbox(points)  # Reject NaN/Infinity before asking geometry libraries to use them.
    shape = feature.geometry()
    parts = shape.asMultiPolygon() if shape.isMultipart() else [shape.asPolygon()]
    vertices = sorted((p.x(), p.y()) for p in parts[0][0][:-1])
    if any(math.dist(a, b) > 1e-7 for a, b in zip(vertices, sorted(points))):
        raise ValueError(f"{tree_id}: geometry and P0–P3 fields disagree")

    mapped = [transform.transform(QgsPointXY(*p)) for p in points]
    points = [[p.x(), p.y()] for p in mapped]
    Kite(*(Point(*p) for p in points)).validate()
    ring = [points[i] for i in (0, 2, 1, 3, 0)]
    key = re.sub(r"[^A-Za-z0-9_-]", "_", tree_id)
    return Tree(tree_id, key, points, ring, polygon(ring), attrs,
                layer.source(), int(feature.id()), layer.crs().toWkt())



def collect_trees(project, options, raster_crs):
    trees, targets, ids, keys, sources = [], [], set(), set(), set()
    index = QgsSpatialIndex()
    for layer_id in options.kite_layer_ids:
        layer = project.mapLayer(layer_id)
        if not is_export_source(layer) or not layer.crs().isValid():
            raise ValueError("a chosen kite layer is missing, invalid, or has no CRS")
        if layer.isEditable():
            raise ValueError(f"save edits and turn editing off for {layer.name()!r}")
        if layer.source() in sources:
            raise ValueError("the same data source is selected twice. choose only one copy")
        sources.add(layer.source())
        transform = QgsCoordinateTransform(layer.crs(), raster_crs, project)
        selected = set(layer.selectedFeatureIds())
        iterator, feature = layer.getFeatures(), QgsFeature()
        try:
            while iterator.nextFeature(feature):
                tree = tree_from_feature(feature, layer, transform)
                if tree.tree_id in ids or tree.key.casefold() in keys:
                    raise ValueError(f"duplicate tree ID or output filename: {tree.tree_id!r}")
                ids.add(tree.tree_id)
                keys.add(tree.key.casefold())
                number = len(trees)
                trees.append(tree)
                item = QgsFeature()
                item.setId(number)
                item.setGeometry(tree.geometry)
                if not index.addFeature(item):
                    raise RuntimeError("could not index a kite")
                if not options.selected or feature.id() in selected:
                    targets.append(number)
                feature = QgsFeature()
        finally:
            iterator.close()
    targets.sort(key=lambda n: trees[n].tree_id)
    if options.limit is not None:
        targets = targets[:options.limit]
    return trees, targets, index

def read_rgb_arrays(dataset, bands, size):
    width, height = size
    valid = np.ones((height, width), dtype=bool)
    arrays = []
    for number in bands:
        band = dataset.GetRasterBand(number)
        array = band.ReadAsArray(buf_xsize=width, buf_ysize=height)
        mask = band.GetMaskBand().ReadAsArray(buf_xsize=width, buf_ysize=height)
        valid &= (mask > 0) & np.isfinite(array)
        arrays.append(array)
    return arrays, valid

def display_ranges(source, options):

    if options.stretch == "byte":
        return [[0.0, 255.0] for _ in range(3)]
    size = png_dimensions(source.RasterXSize, source.RasterYSize, 1024)
    arrays, valid = read_rgb_arrays(source, options.rgb_bands, size)
    ranges = []
    for array in arrays:
        low, high = map(float, np.percentile(array[valid], [2, 98]))
        ranges.append([low, high if high > low else low + 1.0])
    return ranges

def offset_window(center_x, center_y, size, minimum, maximum, rng,):

    angle = rng.uniform(0.0, math.tau)

    radius = math.sqrt(
        rng.uniform(minimum**2, maximum**2)
    )

    shifted_x = center_x + radius * math.cos(angle)
    shifted_y = center_y + radius * math.sin(angle)

    left = round(shifted_x - size / 2)
    top = round(shifted_y - size / 2)

    return [left, top, size, size]

def make_sample(target, 
                trees,
                index,
                source,
                transform,
                options, 
                *, 
                used_windows, 
                rng, 
                kind="centered", 
                ):
    """ builds one centered or offset sample of the raster 
    corresponding to target tree return None when center crop was already exported 
    retreis duplicates or unsuiotable offset windows 
    the caller records the window in used winmdows after saving to avoid duplicate 
    """

    if kind == "centered":
        
        # get requested window from raster given target
        window, padding_clipped = crop_window(
            target.ring,
            transform,
            source.RasterXSize,
            source.RasterYSize,
            options.padding_fraction,
            options.min_padding_pixels,
            options.fixed_size if options.crop_mode == "fixed" else None,
            square=options.crop_mode == "square", 
            )
        attempts = 1 
    else:
        if options.crop_mode != "fixed":
            raise ValueError("offset sampling requires fixed crop window")

        size = options.fixed_size

        # locate trees bounding box in source pixels 
        x0, y0, x1, y1 = pixel_bbox([
            world_to_pixel(point, transform)
            for point in target.ring 
            ])

        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) /2 

        padding_clipped = None
        attempts = options.sample_max_attempts 

    for _ in range(attempts):
        if kind == "offset":
            window = offset_window(
                    center_x, 
                    center_y, 
                    size, 
                    options.offset_min_px, 
                    options.offset_max_px, 
                    rng
                    )


        left, top, width, height = window

        # reject window outside of raster
        if (
                left < 0 
                or top < 0
                or left + width > source.RasterXSize 
                or top + height > source.RasterYSize
                ):
            continue 
        # share this set across every exported tree
        key = tuple(window)

        if key in used_windows:
            if kind == "centered":
                return None 
            continue 


        crop_transform = shifted_transform(transform, left, top)
        corners = [pixel_to_world(p, crop_transform) for p in bbox_ring([0, 0, width, height])]
        footprint = polygon(corners)

        nearby = [] 

        candidates = index.intersects(footprint.boundingBox())
        
        for number in sorted(candidates):
            tree = trees[number]
            if not tree.geometry.intersects(footprint):
                continue
            label = pixel_label(
                    tree.points,
                    tree.ring,
                    crop_transform,
                    width,
                    height
                    )
            x0, y0, x1, y1 = label["bbox_xyxy_px"]
            if x1 > x0 and y1 > y0:
                label.update(
                        tree_id=tree.tree_id,
                        is_target=tree.tree_id == target.tree_id
                        )
                nearby.append((number, label))
        target_labels = [label for _, label in nearby if label["is_target"]]
        require_whole_target = (kind == "centered" or not options.allow_partial_trees)

        if require_whole_target:
            if (len(target_labels) != 1 
                or target_labels[0]["truncated"]
                ):
                if kind == "centered":
                    raise ValueError("target would be missing or truncated")
                continue 

        return {
                "sample_id": (
                    f"{target.key}_{kind}_"
                    f"{left}_{top}_{width}_{height}"
                    ), 
                "sample_kind": kind, 
                "target_tree_id": target.tree_id,
                "window": window,
                "geotransform": crop_transform,
                "corners": corners,
                "padding_clipped": padding_clipped,
                "nearby": nearby
                }
        raise ValueError(f"no unique valid {kind} window after {attempts} attempts")



def write_tiff(source, path, sample):
    image = gdal.Translate(str(path), source, options=gdal.TranslateOptions(
        format="GTiff", srcWin=sample["window"], overviewLevel="NONE", strict=True,
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"]))
    image.FlushCache()
    return image


def make_png(crop, options, ranges):
    size = png_dimensions(crop.RasterXSize, crop.RasterYSize, options.png_max_size)
    arrays, valid = read_rgb_arrays(crop, options.rgb_bands, size)
    rgb = stretch_rgb(arrays, valid, ranges)
    raw = rgb.tobytes()
    image = QImage(raw, size[0], size[1], size[0] * 3, QImage.Format.Format_RGB888).copy()
    return image, float(valid.mean())


def write_png(image, path):
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"could not save {path.name}")


def draw_review(image, annotations):
    review = image.copy()
    painter = QPainter(review)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        for ann in annotations:
            points = [ann["keypoints"][i:i + 3] for i in range(0, 12, 3)]
            painter.setPen(QPen(QColor("red"), 1))
            for a, b in ((0, 2), (2, 1), (1, 3), (3, 0)):
                if points[a][2] and points[b][2]:
                    painter.drawLine(QPointF(*points[a][:2]), QPointF(*points[b][:2]))
            for name, (x, y, visible) in zip(("base", "tip", "left", "right"), points):
                if visible:
                    painter.drawEllipse(QPointF(x, y), 3, 3)
                    painter.drawText(QPointF(x + 4, y - 4), name)
            painter.setPen(QPen(QColor("red"), 1))
            if points[0][2] and points[1][2]:
                painter.drawLine(QPointF(*points[0][:2]), QPointF(*points[1][:2]))
    finally:
        painter.end()
    return review


def write_vector_layer(path, name, geometry_type, rows, crs, first):
    layer = QgsVectorLayer(geometry_type, name, "memory")
    layer.setCrs(crs)
    fields = [QgsField("tree_id", QMetaType.Type.QString),
              QgsField("ta_export", QMetaType.Type.Int),
              QgsField("details_json", QMetaType.Type.QString)]
    provider = layer.dataProvider()
    layer.updateFields()
    features = []
    for tree_id, geometry, details in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([tree_id, 1, json.dumps(details, allow_nan=False)])
        feature.setGeometry(geometry)
        features.append(feature)
    ok, added = provider.addFeatures(features)
    if not ok or len(added) != len(features):
        raise RuntimeError(f"cannot prepare {name} features")
    settings = QgsVectorFileWriter.SaveVectorOptions()
    settings.driverName, settings.layerName = "GPKG", name
    settings.actionOnExistingFile = (QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        if first else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(path), QgsProject.instance().transformContext(), settings)


def write_vectors(path, trees, used, samples, crs, transform):
    kites, lines, boxes = [], [], []
    for number in sorted(used):
        tree = trees[number]
        details = {"source_layer": tree.source_layer, "source_fid": tree.source_fid,
                   "source_crs_wkt": tree.input_crs, "attributes": tree.attributes,
                   "keypoints_world_p0_p1_p2_p3": tree.points}
        kites.append((tree.tree_id, tree.geometry, details))
        lines.append((
            tree.tree_id, 
            QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in tree.points[:2]]),
            details))
        box = pixel_bbox([world_to_pixel(p, transform) for p in tree.ring])
        boxes.append((
            tree.tree_id,
            polygon([pixel_to_world(p, transform) for p in bbox_ring(box)]), details)
            )
    crops = [(s["target_tree_id"], polygon(s["corners"]), s) for s in samples]
    for i, (name, kind, rows) in enumerate((
            ("kites", "Polygon", kites), ("fall_vectors", "LineString", lines),
            ("bounding_boxes", "Polygon", boxes), ("crop_bounds", "Polygon", crops))):
        write_vector_layer(path, name, kind, rows, crs, i == 0)


def write_split_groups(path, samples):
    groups = split_groups(samples)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file_name", "target_tree_id", "split_group"])
        for sample, group in zip(samples, groups):
            sample["split_group"] = group
            writer.writerow([Path(sample["png"]).name, sample["target_tree_id"], group])

