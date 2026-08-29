""" 

persistence for annkites (annotation kites) and derived fall_vectors 


"""

from __future__ import annotations 

from pathlib import Path 
from dataclasses import dataclass 
from typing import Literal 
from datetime import datetime, timezone 
from uuid import uuid4

from qgis.PyQt.QtCore import QVariant 
from qgis.core import (
        Qgis, 
        QgsArrowSymbolLayer, 
        QgsCoordinateReferenceSystem, 
        QgsFeature, 
        QgsFeatureRequest, 
        QgsField, 
        QgsFields, 
        QgsFillSymbol, 
        QgsGeometry, 
        QgsLineSymbol, 
        QgsPointXY, 
        QgsProject, 
        QgsSingleSymbolRenderer,
        QgsVectorDataProvider, 
        QgsVectorFileWriter, 
        QgsVectorLayer
        )

from .geometry import KiteMetrics 
from .damage_classes import DamageClass


LayerSpecies = Literal["fallvec", "annkite"]

SCHEMA_VERSION = "2.0"
DEFAULT_LAYER_NAME = "kites"
DEFAULT_VECTOR_NAME = "fall_vectors"
FALL_VECTOR_SUFFIX = "_fall_vectors"
FALL_VECTOR_FIELDS = (
    "tree_id",
    "schema_version",
    "tree_h_m",
    "fall_az",
    "fall_dir",
    "wind_from",
    "damage_class_id",
    "damage_class_name",
    "created_at",
    "updated_at",
    "source_name",
    "source_uri",
    "source_crs",
    "mosaic_id",
    "bundle_id",
    "tile_id",
)
ROLE_FIELDS = (
        ("p0_x","p0_y"), # base 
        ("p1_x", "p1_y"), # tip
        ("p2_x", "p2_y"), # left crown 
        ("p3_x", "p3_y"), # right crown 
        )

FALL_ARROW = {
        "start_width": 0.35, 
        "width": 0.6, 
        "head_length": 2.8, 
        "head_thickness": 1.2, 
        "color": "211, 47, 47, 235"
        } 

KITE = {
        "color": "255,152,0,45",
        "outline_color": "239,108,0,235",
        "outline_width": "0.6"
        }


def field_names(layer: LayerSpecies) -> set[str]:
    if layer == "fallvec":
        fields = _fall_vector_fields()
    elif layer == "annkite":
        fields = _ann_kite_fields()
    else:
        raise ValueError(f"Unknown layer species: {layer}")

    return {
        field.name()
        for field in fields.toList()
    }
#======================================================================================#
# ANNOTATION LAYER 
#======================================================================================#

@dataclass(frozen=True, slots=True)
class RasterSource: 
    layer_id: str = ""
    name: str = ""
    uri: str = ""
    crs_authid: str = ""

def _ann_kite_fields() -> QgsFields: 
    fields = QgsFields()
    definitions = (
            QgsField("tree_id", QVariant.String, len=36),
            QgsField("schema_version", QVariant.String, len=12),
            QgsField("p0_x", QVariant.Double),
            QgsField("p0_y", QVariant.Double),
            QgsField("p1_x", QVariant.Double),
            QgsField("p1_y", QVariant.Double),
            QgsField("p2_x", QVariant.Double),
            QgsField("p2_y", QVariant.Double),
            QgsField("p3_x", QVariant.Double),
            QgsField("p3_y", QVariant.Double),
            QgsField("tree_h_m", QVariant.Double),
            QgsField("crown_w_m", QVariant.Double),
            QgsField("fall_az", QVariant.Double),
            QgsField("fall_dir", QVariant.String, len=4),
            QgsField("wind_from", QVariant.Double),
            QgsField("kite_area", QVariant.Double),
            QgsField("damage_class_id", QVariant.String, len=36),
            QgsField("damage_class_name", QVariant.String, len=100),
            QgsField("failure_mode", QVariant.String, len=32),
            QgsField("branch_loss_class", QVariant.String, len=24),
            QgsField("root_plate_visible", QVariant.String, len=8),
            QgsField("crown_intactness", QVariant.String, len=24),
            QgsField("exposure", QVariant.String, len=12),
            QgsField("ground_type", QVariant.String, len=16),
            QgsField("health_of_tree", QVariant.String, len=16),
            QgsField("tree_type", QVariant.String, len=16),
            QgsField("confidence", QVariant.String, len=12),
            QgsField("notes", QVariant.String),
            QgsField("annotator", QVariant.String, len=100),

            QgsField("created_at", QVariant.String, len=32),
            QgsField("updated_at", QVariant.String, len=32),
            QgsField("source_name", QVariant.String),
            QgsField("source_uri", QVariant.String),
            QgsField("source_crs", QVariant.String, len=64),
            QgsField("mosaic_id", QVariant.String, len=128),
            QgsField("bundle_id", QVariant.String, len=128),
            QgsField("tile_id", QVariant.String, len=128),
        )
    for field in definitions:
        fields.append(field)
    return fields

def is_kite(layer) -> bool: 
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return False 
    if layer.geometryType() != Qgis.GeometryType.Polygon: 
        return False 
    names = (field.name() for field in layer.fields().toList()) 
    return field_names("annkite").issubset(names)

def open_layer(
        path: str, 
        crs: QgsCoordinateReferenceSystem, 
        source: RasterSource, 
        layer_name: str = DEFAULT_LAYER_NAME
        ) -> QgsVectorLayer:
    """ create or open annotation layer for kite creation """

    path_obj = Path(path).expanduser().resolve()
    uri = f"{path_obj}|layername={layer_name}"
    
    # hand existing package
    if path_obj.exists():
        existing = QgsVectorLayer(uri, layer_name, "ogr")
        if not existing.isValid():
            raise ValueError(
                    "the selected geopackage does not have kite layer." 
                    "choose a new filename; this program will not modify older file."
                    )
        if not is_kite(existing):
            missing = sorted(
                    field_names("annkite")
                    - {
                        field.name() 
                        for field in existing.fields()
                        }
                    ) 
            raise ValueError(
                    "the existing layer is not a compatable kite layer:"
                    f"missing: {','.join(missing)}"
                    )
        _configure_kite_layer(existing, source)
        return existing 
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    memory = QgsVectorLayer("Polygon", layer_name, "memory")
    memory.setCrs(crs)
    data_provider(memory).addAttributes(list(_ann_kite_fields().toList()))
    memory.updateFields()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name 
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile =(
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
            )
    project = QgsProject.instance()
    if project is None: 
        raise RuntimeError("cannot find project instance...")
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
            memory, 
            str(path_obj), 
            project.transformContext(), 
            options,
        )
    if result[0] != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"could not create geopackage layer: {result[1]}")

    layer = QgsVectorLayer(uri, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not open the new layer at {path_obj}")

    _configure_kite_layer(layer, source)
    return layer 

def _configure_kite_layer(layer: QgsVectorLayer, source: RasterSource) -> None: 
    layer.setCustomProperty("treeangle/schema_version", SCHEMA_VERSION) 
    layer.setCustomProperty("treeangle/geometry_model","four_point_kite")
    layer.setCustomProperty("treeangle/source_layer_id", source.layer_id)
    layer.setCustomProperty("treeangle/source_name", source.name)
    layer.setCustomProperty("treeangle/source_uri",source.uri)
    layer.setCustomProperty("treeangle/source_crs", source.crs_authid)

    aliases = {
            "p0_x": "base x", 
            "p0_y": "base y", 
            "p1_x": "tip x",
            "p1_y": "top y",
            "p2_x": "crown_l x",
            "p2_y": "crown_l y",
            "p3_x": "crown_r x",
            "p3_y": "crown_r y",
            "tree_h_m": "tree height/major axis (m)",
            "crown_w_m": "crown width/minor axis (m)",
            "fall_az": "fall azimuth (degrees)",
            "wind_from": "inferred wind direction from fall_az (degrees)",
            "kite_area": "kite area (sqm)"
            } 

    for field_name, alias in aliases.items():
        index = layer.fields().indexOf(field_name)
        if index >= 0: 
            layer.setFieldAlias(index, alias)

    symbol = QgsFillSymbol.createSimple(
            {
                "color": KITE["color"], 
                "outline_color": KITE["outline_color"], 
                "outline_width": KITE["outline_width"]
            }

            )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()

def source_from(layer: QgsVectorLayer) -> RasterSource:  
    return RasterSource(
        layer_id=str(layer.customProperty("treeangle/source_layer_id", "")),
        name=str(layer.customProperty("treeangle/source_name", "")),
        uri=str(layer.customProperty("treeangle/source_uri", "")),
        crs_authid=str(layer.customProperty("treeangle/source_crs", "")),
    )

def points_from(feature: QgsFeature) -> list[QgsPointXY]:
    return [
            QgsPointXY(float(feature[x_field]), float(feature[y_field]))
            for x_field, y_field in ROLE_FIELDS
            ]

def kite_geometry(points: list[QgsPointXY]) -> QgsGeometry: 
    if len(points) != 4: 
        raise ValueError("expects 4 points to build a kite!")
    base, tip, left, right = points 
    return QgsGeometry.fromPolygonXY([[base, left, tip, right, base]])

def create_kite_feature(
        layer: QgsVectorLayer, 
        points: list[QgsPointXY],
        measurements: KiteMetrics, 
        damage_class: DamageClass, 
        source: RasterSource,
    ) -> QgsFeature:
    now = _utc_now() 
    values: dict[str, object] = {
            "tree_id": str(uuid4()), 
            "schema_version": SCHEMA_VERSION, 
            "damage_class_id": damage_class.class_id, 
            "damage_class_name": damage_class.name, 
            "created_at": now, 
            "updated_at": now, 
            "source_name": source.name, 
            "source_uri": source.uri, 
            "source_crs": source.crs_authid or layer.crs().authid(), 
            "mosaic_id": damage_class.mosaic_id, 
            "bundle_id": damage_class.bundle_id,
            "tile_id": damage_class.tile_id,
            **_point_values(points),
            **_metric_values(measurements),
            **damage_class.attributes.as_storage_dict(),
        }
    feature = QgsFeature(layer.fields())
    feature.setGeometry(kite_geometry(points))
    for name, value in values.items():
        if layer.fields().indexOf(name) >= 0: 
            feature[name] = value 
    return feature 

def add_feature_now(layer: QgsVectorLayer, feature: QgsFeature) -> int: 
    if layer.isEditable():
        if not layer.addFeature(feature):
            raise RuntimeError("qgis rejected this new feature...")
        return feature.id() 
    success, added = data_provider(layer).addFeatures([feature])
    if not success or not added:
        raise RuntimeError("the geopackage provider rejected the new feature...")
    layer.updateExtents()
    layer.triggerRepaint()
    return added[0].id()

def delete_feature_now(layer: QgsVectorLayer, feature_id: int) -> None: 
    if layer.isEditable():
        success = layer.deleteFeature(feature_id)
    else: 
        success = data_provider(layer).deleteFeatures([feature_id])
    if not success: 
        raise RuntimeError("qgis could not roll back the annotation feature...")

    layer.updateExtents()
    layer.triggerRepaint()

def update_kite_geometry(
        layer: QgsVectorLayer, 
        feature_id: int, 
        points: list[QgsPointXY], 
        measurements: KiteMetrics, 
        ) -> None: 
    geometry = kite_geometry(points)
    values = {
            **_point_values(points), 
            **_metric_values(measurements), 
            "updated_at": _utc_now()
            }
    indexed_values = {
            layer.fields().indexOf(name): value 
            for name, value in values.items() 
            if layer.fields().indexOf(name) >= 0
            }
    if layer.isEditable():
        layer.beginEditCommand("move kite points as needed")
        geometry_ok = layer.changeGeometry(feature_id, geometry)
        attributes_ok = all(
                layer.changeAttributeValue(feature_id, index, value)
                for index, value in indexed_values.items()
                )
        if geometry_ok and attributes_ok: 
            layer.endEditCommand()
        else:
            layer.destroyEditCommand()
            raise RuntimeError("qgis coult not update selection...")
    else: 
        provider = data_provider(layer)
        geometry_ok = provider.changeGeometryValues({feature_id: geometry})
        attributes_ok = provider.changeAttributeValues({feature_id: indexed_values})
        if not (geometry_ok and attributes_ok):
            raise RuntimeError("the gpkg provider could not update selection")

    layer.triggerRepaint()

def _point_values(points: list[QgsPointXY]) -> dict[str, float]:
    if len(points) != 4:
        raise ValueError("a kite needs four points!")
    values: dict[str, float] = {}
    for point, (x_field, y_field) in zip(points, ROLE_FIELDS):
        values[x_field] = point.x()
        values[y_field] = point.y()
    return values 

def _metric_values(measurements: KiteMetrics) -> dict[str, object]:
    
    return {
            "tree_h_m": measurements.tree_height_m, 
            "crown_w_m": measurements.crown_width_m, 
            "fall_az": measurements.fall_azimuth_deg, 
            "fall_dir": measurements.fall_direction,
            "wind_from": measurements.wind_from, 
            "kite_area": measurements.kite_area_sqm
            }

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def data_provider(layer: QgsVectorLayer) -> QgsVectorDataProvider: 
    provider = layer.dataProvider()

    if provider is None: 
        raise RuntimeError(
                f"no data provider can be found for layer: {layer.name()}"
                )
    return provider 
    

#======================================================================================#
# FALL_VECTORS #
# =====================================================================================#
def layer_storage_path(layer: QgsVectorLayer) -> Path: 
    source_path = layer.source().split("|", maxsplit=1)[0]
    if not source_path: 
        raise ValueError("the annotation layer does not have a file-backed source")
    return Path(source_path).expanduser().resolve()

def fall_vector_path(annotation: str | Path | QgsVectorLayer) -> Path: 
    if isinstance(annotation, QgsVectorLayer):
        return fall_vector_path(layer_storage_path(annotation))
    path = Path(annotation).expanduser().resolve()
    return path.with_name(f"{path.stem}{FALL_VECTOR_SUFFIX}.gpkg")

def _fall_vector_fields() -> QgsFields: 
    fields = QgsFields()
    definitions = (
            QgsField("tree_id", QVariant.String, len=36),
            QgsField("schema_version", QVariant.String, len=12),
            QgsField("tree_h_m", QVariant.Double),
            QgsField("fall_az", QVariant.Double),
            QgsField("fall_dir", QVariant.String, len=4),
            QgsField("wind_from", QVariant.Double),
            QgsField("damage_class_id", QVariant.String, len=36),
            QgsField("damage_class_name", QVariant.String, len=100),
            QgsField("created_at", QVariant.String, len=32),
            QgsField("updated_at", QVariant.String, len=32),
            QgsField("source_name", QVariant.String),
            QgsField("source_uri", QVariant.String),
            QgsField("source_crs", QVariant.String, len=64),
            QgsField("mosaic_id", QVariant.String, len=128),
            QgsField("bundle_id", QVariant.String, len=128),
            QgsField("tile_id", QVariant.String, len=128),
            )

    for field in definitions: 
        fields.append(field)
    return fields 



def is_fall_vector(layer) -> bool: 
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return False 
    if layer.geometryType() != Qgis.GeometryType.Line: 
        return False 
    names = {field.name() for field in layer.fields()}
    return field_names("fallvec").issubset(names)

def open_fall_vectors(
        path: str | Path, 
        crs: QgsCoordinateReferenceSystem, 
        layer_name: str = DEFAULT_VECTOR_NAME, 
        ) -> QgsVectorLayer: 
    """ open or create a new compation gpkg """

    path_obj = Path(path).expanduser().resolve()
    uri = f"{path_obj}|layername={layer_name}"
    if path_obj.exists():
        existing = QgsVectorLayer(uri, layer_name, "ogr")
        if not existing.isValid():
            raise ValueError(
                    "the existing fall-vector gpkg is not valid with this schema version"
                    )
        if not is_fall_vector(existing):
            missing = sorted(
                    field_names("fallvec")
                    - {field.name() for field in existing.fields()}
                    )
            detail = f"missing fields: {','.join(missing)}" if missing else "not a line layer"

            raise ValueError(f"the existing fall-vector layer is incompatible: {detail}")

        _configure_fall_vector_layer(existing)
        return existing 

    path_obj.parent.mkdir(parents=True, exist_ok=True)
    memory = QgsVectorLayer("LineString", layer_name, "memory")
    memory.setCrs(crs)
    data_provider(memory).addAttributes(list(_fall_vector_fields().toList()))
    memory.updateFields()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
    )
    project = QgsProject.instance()
    if project is None: 
        raise RuntimeError("cannot find project instance...")
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        memory,
        str(path_obj),
        project.transformContext(),
        options,
    )
    if result[0] != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"Could not create fall-vector GeoPackage: {result[1]}")

    layer = QgsVectorLayer(uri, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"QGIS could not open the fall-vector layer at {path_obj}")

    _configure_fall_vector_layer(layer)
    return layer


def _configure_fall_vector_layer(layer: QgsVectorLayer) -> None: 
        layer.setCustomProperty("treeangle/schema_version", SCHEMA_VERSION)
        layer.setCustomProperty("treeangle/layer_role","fall_vectors")

        aliases = {
                "tree_h_m": "tree height or vector length (m)", 
                "fall_az": "fall azimuth (degrees)", 
                "wind_from": "inferred wind direction from azimuth (degrees)"
                }

        for field_name, alias in aliases.items():
            index = layer.fields().indexOf(field_name)
            if index >= 0: 
                layer.setFieldAlias(index, alias)

        arrow = QgsArrowSymbolLayer()
        arrow.setArrowStartWidth(FALL_ARROW["start_width"])
        arrow.setArrowWidth(FALL_ARROW["width"])
        arrow.setHeadLength(FALL_ARROW["head_length"])
        arrow.setHeadThickness(FALL_ARROW["head_thickness"])
        arrow.setIsCurved(False)
        arrow.setIsRepeated(False)
        arrow_fill = QgsFillSymbol.createSimple(
                {"color": str(FALL_ARROW["color"])}  
                )
        arrow.setSubSymbol(arrow_fill)
        arrow_symbol = QgsLineSymbol()
        arrow_symbol.changeSymbolLayer(0, arrow)
        layer.setRenderer(QgsSingleSymbolRenderer(arrow_symbol))
        layer.triggerRepaint()

def fall_vector_geometry(points: list[QgsPointXY]) -> QgsGeometry: 
        """ build major acis: p0 base, p1 tip"""

        if len(points) != 4: 
            raise ValueError(" fall vector requires four kite points")

        return QgsGeometry.fromPolylineXY([points[0], points[1]])

def create_fall_vector_feature(
        vector_layer: QgsVectorLayer, 
        annotation_feature: QgsFeature, 
        points: list[QgsPointXY], 
    ) -> QgsFeature: 
    values = _copied_values(annotation_feature)
    values["tree_id"] = _tree_id(annotation_feature)
    values.setdefault("schema_version", SCHEMA_VERSION)
    feature = QgsFeature(vector_layer.fields())
    feature.setGeometry(fall_vector_geometry(points))
    for name, value in values.items():
        if vector_layer.fields().indexOf(name) >= 0:
            feature[name] = value
    return feature

def upsert_fall_vector(
        vector_layer: QgsVectorLayer, 
        annotation_feature: QgsFeature, 
        points: list[QgsPointXY]
    ) -> int: 
    tree_id = _tree_id(annotation_feature)
    replacement = create_fall_vector_feature(vector_layer, annotation_feature, points)
    existing = _feature_for_tree_id(vector_layer, tree_id)
    if existing is None:
        return _add_vector_feature(vector_layer, replacement)
    feature_id = int(existing.id())
    _update_vector_feature(vector_layer, feature_id, replacement)
    return feature_id

def synchronize(
    annotation_layer: QgsVectorLayer,
    vector_layer: QgsVectorLayer,
) -> int:
    annotation_tree_ids: set[str] = set()
    count = 0

    iterator = annotation_layer.getFeatures()
    annotation_feature = QgsFeature()

    try:
        while iterator.nextFeature(annotation_feature):
            try:
                tree_id = _tree_id(annotation_feature)
                points = points_from(annotation_feature)
            except (TypeError, ValueError) as error:
                feature_id = int(
                    annotation_feature.id()
                )
                raise ValueError(
                    "Could not reconstruct fall vector "
                    f"for annotation {feature_id}: {error}"
                ) from error

            annotation_tree_ids.add(tree_id)

            upsert_fall_vector(
                vector_layer,
                annotation_feature,
                points,
            )

            count += 1
            annotation_feature = QgsFeature()
    finally:
        iterator.close()

    orphan_ids: list[int] = []

    iterator = vector_layer.getFeatures()
    vector_feature = QgsFeature()

    try:
        while iterator.nextFeature(vector_feature):
            tree_id = str(
                vector_feature["tree_id"]
            ).strip()

            if tree_id not in annotation_tree_ids:
                orphan_ids.append(
                    int(vector_feature.id())
                )

            vector_feature = QgsFeature()
    finally:
        iterator.close()

    for feature_id in orphan_ids:
        delete_feature_now(
            vector_layer,
            feature_id,
        )

    return count

def _tree_id(annotation_feature: QgsFeature) -> str: 
        tree_id = str(annotation_feature["tree_id"]).strip()
        if not tree_id: 
            raise ValueError("the annotation feature does not have a tree_id")
        return tree_id 

def _copied_values(annotation_features: QgsFeature) -> dict[str, object]:
        annotation_names = {
                field.name() 
                for field in annotation_features.fields().toList() 
                }
        return {
                name: annotation_features[name]
                for name in FALL_VECTOR_FIELDS 
                if name in annotation_names
            }

def _feature_for_tree_id(
    vector_layer: QgsVectorLayer,
    tree_id: str,
) -> QgsFeature | None:
    escaped = tree_id.replace("'", "''")

    request = QgsFeatureRequest().setFilterExpression(
        f'"tree_id" = \'{escaped}\''
    )
    request.setLimit(1)

    iterator = vector_layer.getFeatures(request)
    feature = QgsFeature()

    try:
        if iterator.nextFeature(feature):
            return feature

        return None
    finally:
        iterator.close()

def _add_vector_feature(vector_layer: QgsVectorLayer, feature: QgsFeature) -> int: 
    if vector_layer.isEditable():
        if not vector_layer.addFeature(feature):
            raise RuntimeError("QGIS reected the new fall vector...")
        vector_layer.triggerRepaint()
        return int(feature.id())
    success, added = data_provider(vector_layer).addFeatures([feature])
    if not success or not added: 
        raise RuntimeError("the geopackage provider rejected the fall vector")
    
    vector_layer.updateExtents()
    vector_layer.triggerRepaint()
    return int(added[0].id())

def _update_vector_feature(
        vector_layer: QgsVectorLayer, 
        vector_feature_id: int, 
        replacement: QgsFeature,
    ) -> None: 

        indexed_values = {
                  vector_layer.fields().indexOf(name): replacement[name]
                  for name in FALL_VECTOR_FIELDS
                  if vector_layer.fields().indexOf(name) >= 0
            } 

        if vector_layer.isEditable():
            vector_layer.beginEditCommand("update fall vector")
            geometry_ok = vector_layer.changeGeometry(vector_feature_id, replacement.geometry())
            attributes_ok = all(
                vector_layer.changeAttributeValue(vector_feature_id, index, value)
                for index, value in indexed_values.items()
            )
            if geometry_ok and attributes_ok:
                vector_layer.endEditCommand()
            else:
                vector_layer.destroyEditCommand()
                raise RuntimeError("QGIS could not update the fall vector")
        else:
            provider = data_provider(vector_layer)
            geometry_ok = provider.changeGeometryValues(
                {vector_feature_id: replacement.geometry()}
            )
            attributes_ok = provider.changeAttributeValues(
                {vector_feature_id: indexed_values}
            )
            if not (geometry_ok and attributes_ok):
                raise RuntimeError("the GeoPackage provider could not update the fall vector")
        vector_layer.updateExtents()
        vector_layer.triggerRepaint()


