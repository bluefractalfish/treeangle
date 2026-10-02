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
from .jobs import ExportJob 

@dataclass(frozen=True)
class ExportOptions: 
    """ settings for one run """ 
    raster_id: str = ""
    kite_layer_ids: tuple[str, ...] = ()
    output_parent: str | None = None 
    limit: int | None = None
    selected: bool = False
    padding_fraction: float = 0.15
    min_padding_pixels: int = 16
    crop_mode: str = "bbox"  # bbox, square, fixed
    fixed_size: int = 512
    max_crop_pixels: int = 16_000_000
    rgb_bands: tuple[int, int, int] = (1,2,3) 
    stretch: str = "percentile"
    png_max_size: int = 0 # 0 = native size, else downsampled only 
    canonicalize_sides: bool = True
    write_review: bool = True

    # other options - noncentered trees, empty patches 
    sample_area_layer_id: str = "" 
    area_sample_count: int = 0 
    offsets_per_tree: int = 0 
    offset_min_px: int = 64
    offset_max_px: int = 496
    allow_partial_trees: bool = True

    sample_seed: int = 37
    sample_max_attempts: int = 500
    

    def validate(self):
        """ validate exportOptions and check for logical consistency """
        if not self.raster_id or not self.kite_layer_ids or not self.output_parent:
            raise ValueError(" choose a raster, kite layer, and output folder")
        if self.crop_mode not in {"bbox", "square", "fixed"}:
            raise ValueError("choose a bounding-box, square, or fixed-size crop")
        if self.stretch not in {"percentile", "byte"}:
            raise ValueError("unknown PNG contrast settings")
        if not math.isfinite(self.padding_fraction) or self.padding_fraction < 0:
            raise ValueError("paddding must be finite and nonnegative")
        for name in ("min_padding_pixels", "fixed_size", "max_crop_pixels", "png_max_size"):
            value = getattr(self, name)
            # Zero means "use the native crop dimensions" for PNG output.
            minimum = 0 if name in {"min_padding_pixels", "png_max_size"} else 1
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.limit is not None and (isinstance(self.limit, bool) or
                                      not isinstance(self.limit, int) or self.limit < 1):
            raise ValueError("limit must be a positive integer or None")
        if len(self.rgb_bands) != 3 or any(type(n) is not int or n<1 for n in self.rgb_bands):
            raise ValueError("inproper values for band numbers, needs to be based 1")
        if self.crop_mode == "fixed" and self.fixed_size ** 2 > self.max_crop_pixels:
            raise ValueError("fixed size exceeds the maximum crop area")


def export_training_pairs(iface, options):
    options.validate()
    previous = getattr(iface, "_treeangle_patch_export", None)
    if previous is not None and not previous.done:
        raise RuntimeError("an export is already exporting")
    job = ExportJob(iface, options)
    iface._treeangle_patch_export = job
    job.progress.show()
    QTimer.singleShot(0, job.prepare)
    return job


