from dataclasses import dataclass, asdict 
from datetime import datetime, timezone
from pathlib import Path
import csv 
import json
import math
import re
import shutil
import math

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

from .collector import (
        open_raster, collect_trees, display_ranges, write_json, make_sample, write_tiff, 
        make_png, coco_annotation, write_png, write_split_groups, write_vectors, draw_review, new_coco
        )



class ExportJob(QObject):
    finished = pyqtSignal(object)

    def __init__(self, iface, options):
        super().__init__(iface.mainWindow())
        self.iface, self.options = iface, options
        self.done = self.cancelled = False
        self.source = self.output_dir = None
        self.position = 0
        self.coco, self.samples, self.errors, self.used = new_coco(), [], [], set()
        self.progress = QProgressDialog("PREPARING KITES", "CANCEL", 0, 0, iface.mainWindow())
        self.progress.setWindowModality(Qt.WindowModality.NonModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.setMinimumDuration(0)
        self.progress.canceled.connect(self.cancel)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)

    def prepare(self):
        if self.done:
            return
        try:
            project = QgsProject.instance()
            raster = project.mapLayer(self.options.raster_id)
            self.source, self.crs, self.transform = open_raster(raster)
            self.trees, self.targets, self.index = collect_trees(
                    project, self.options, self.crs
                    )
            self.ranges = display_ranges(self.source, self.options)
            stamp = datetime.now(timezone.utc).strftime("run_%y%m%d_%fZ")
            self.output_dir = Path(self.options.output_parent).expanduser() / stamp
            self.output_dir.mkdir(parents=True, exist_ok=False)
            for name in ("png", "tif", "_pending"):
                (self.output_dir / name).mkdir()
            if self.options.write_review:
                (self.output_dir / "review").mkdir()
            (self.output_dir / "INCOMPLETE.txt").write_text(
                    "wait for the export to finish before uploading\n"
                    )
            self.run = {"options": asdict(self.options),
                        "raster_source": raster.source(),
                        "raster_crs_wkt": self.crs.toWkt(),
                        "source_geotransform": self.transform,
                        "rgb_ranges": self.ranges,
                        "keypoint_roles": ["base", "tip", "left", "right"],
                        "pixel_convention": "continuous pixel edges, origin at top left",
                        "assumption": "only in frame clicked points are marked visible (2);"
                        "outside points missing (0)"
                        }
            write_json(self.output_dir / "run.json", self.run)
            self.progress.setRange(0, len(self.targets))
            self.timer.start(0)
        except Exception as error:
            self.errors.append({"stage": "preparing to fly", "error": str(error)})
            self.finish()


    def export_one(self, number):
        tree = self.trees[number]
        sample = make_sample(tree, 
                             self.trees,
                             self.index,
                             self.source,
                             self.transform,
                             self.options
                             )
        pending = self.output_dir / "_pending"
        files = [(pending / f"{tree.key}.tif", self.output_dir / "tif" / f"{tree.key}.tif"),
                 (pending / f"{tree.key}.png", self.output_dir / "png" / f"{tree.key}.png")]
        crop = None
        try:
            crop = write_tiff(self.source, files[0][0], sample)
            image, coverage = make_png(crop, self.options, self.ranges)
            crop = None  # close the GeoTIFF before moving it
            image_id = len(self.coco["images"]) + 1
            annotations, links = [], []
            for number, label in sample["nearby"]:
                annotation_id = len(self.coco["annotations"]) + len(annotations) + 1
                ann, order = coco_annotation(
                        label,
                        image_id,
                        annotation_id,
                        sample["window"][2:],
                        (image.width(), image.height()), 
                        self.options.canonicalize_sides
                        )
                annotations.append(ann)
                links.append(
                        {
                        "annotation_id": annotation_id,
                        "tree_id": self.trees[number].tree_id,
                        "is_target": label["is_target"], "truncated": label["truncated"],
                        "coco_order_as_original_p_indices": order, "native_label": label
                        }
                    )
            write_png(image, files[1][0])
            if files[1][0].stat().st_size > 20_000_000:
                raise ValueError("PNG exceeds 20 MB set a smaller maximum PNG edge")
            if self.options.write_review:
                files.append(
                        (pending / f"{tree.key}.review.png",
                         self.output_dir / "review" / f"{tree.key}.png")
                        ) 
                write_png(draw_review(image, annotations), files[-1][0])
            for temporary, destination in files:
                temporary.replace(destination)
        except Exception:
            crop = None
            for temporary, destination in files:
                for path in (temporary, destination):
                    path.unlink(missing_ok=True)
            raise
        self.used.update(n for n, _ in sample.pop("nearby"))
        sample.update(image_id=image_id,
                      png=f"png/{tree.key}.png",
                      tif=f"tif/{tree.key}.tif",
                      png_size=[
                          image.width(),
                          image.height()
                          ],
                      valid_rgb_fraction=coverage,
                      annotations=links
                      )
        self.samples.append(sample)
        self.coco["images"].append({"id": image_id, "file_name": f"{tree.key}.png",
                                    "width": image.width(), "height": image.height()})
        self.coco["annotations"].extend(annotations)

    def step(self):
        if self.done:
            return
        if self.cancelled or self.position == len(self.targets):
            self.finish()
            return
        number = self.targets[self.position]
        self.progress.setLabelText(
                f"{self.position + 1}/{len(self.targets)}: {self.trees[number].tree_id}"
                )
        try:
            self.export_one(number)
        except OSError as error:
            self.errors.append({"tree_id": self.trees[number].tree_id, "error": str(error)})
            self.cancelled = True  # Stop repeated writes after a filesystem failure.
        except Exception as error:
            self.errors.append({"tree_id": self.trees[number].tree_id, "error": str(error)})
        self.position += 1
        self.progress.setValue(self.position)
        if self.position % 50 == 0:
            try:
                self.save_labels()
            except OSError as error:
                self.errors.append({"stage": "checkpoint", "error": str(error)})
                self.cancelled = True
        self.timer.start(0)

    def save_labels(self):
        write_json(self.output_dir / "png" / "_annotations.coco.json", self.coco)
        write_json(self.output_dir / "manifest.json", {
            "cancelled": self.cancelled, "exported": len(self.samples),
            "errors": self.errors, "samples": self.samples})

    def cancel(self):
        if not self.done:
            self.cancelled = True
            self.timer.stop()
            self.finish()

    def finish(self):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        finalized = False
        try:
            if self.output_dir is not None:
                write_json(self.output_dir / "png" / "_annotations.coco.json", self.coco)
                write_split_groups(self.output_dir / "split_groups.csv", self.samples)
                if self.samples:
                    write_vectors(self.output_dir / "kites.gpkg", self.trees, self.used,
                                  self.samples, self.crs, self.transform)
                self.save_labels()
                # GDAL may leave harmless auxiliary files here; never delete unrelated data.
                if not any((self.output_dir / "_pending").iterdir()):
                    (self.output_dir / "_pending").rmdir()
                (self.output_dir / "INCOMPLETE.txt").unlink(missing_ok=True)
                finalized = True
        except Exception as error:
            self.errors.append({"stage": "finish", "error": str(error)})
            if self.output_dir is not None:
                try:
                    self.save_labels()
                except Exception as manifest_error:
                    self.errors.append({"stage": "manifest", "error": str(manifest_error)})
        finally:
            self.source = None
            self.progress.close()
            self.progress.deleteLater()
            if getattr(self.iface, "_treeangle_patch_export", None) is self:
                self.iface._treeangle_patch_export = None
            result = {"output_dir": str(self.output_dir or ""), "exported": len(self.samples),
                      "failed": len(self.errors), "cancelled": self.cancelled,
                      "finalized": finalized, "errors": self.errors}
            for error in self.errors:
                print(f"TreeAngle export: {error}")
            self.finished.emit(result)
            self.deleteLater()

