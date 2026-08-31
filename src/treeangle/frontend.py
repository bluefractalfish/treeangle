""" the interface handles the creation and navegation of all QGIS tools 

    
    FormDialog: dialog for damage form creation and selection 
    CaptureTool: handles annotations and draws preview 
    EditTool: handles selecting and dragging points when editing existing Kite


"""

from __future__ import annotations 


from enum import Enum  
from math import hypot
from typing import Callable  

from qgis.PyQt.QtCore import Qt, pyqtSignal, QSettings, QPoint
from qgis.PyQt.QtGui import QColor, QCursor, QKeyEvent
from qgis.gui import (
        QgsMapMouseEvent,
        QgsMapCanvas, 
        QgsMapTool, 
        QgsRubberBand
        )



from qgis.PyQt.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QVBoxLayout,
        QWidget,
)

from qgis.core import (
        Qgis, 
        QgsFeature, 
        QgsFeatureRequest, 
        QgsGeometry, 
        QgsPointXY, 
        QgsRectangle, 
        QgsVectorLayer,
    )


from .layers import upsert_fall_vector, points_from, update_kite_geometry
from .damage_classes import (

        AttributeForm,
        BranchLoss, 
        Confidence, 
        DamageClass, 
        Exposure,
        FailureMode, 
        GroundType,
        Health, 
        Intactness,
        TreeType, 
        display_name, 
        Ternary

        ) 

from .geometry import measure 

__all__ = [
        "FormDialog", 
        "CaptureTool", 
        "EditTool"
        ]

ANNOTATOR_KEY = "treeangle/annotator"



KITE_STROKE = QColor(255, 0, 0, 35)
KITE_FILL = QColor(0, 255, 0, 25) 
KITE_WIDTH = 1 
KITE_MINOR = QColor(45, 125, 50, 24)
KITE_MAJOR = QColor(211,47,47,24)
KITE_VERTEX = QColor(20,20,20,24)



EDIT_STROKE = QColor(25, 118, 210, 24)
EDIT_FILL = QColor(25, 118, 210, 35)
EDIT_STROKE_WIDTH = 1 


class FormDialog(QDialog): 
    """ collect a named template or prefill from active class """
    
    def __init__(
            self, 
            parent: QWidget | None = None, 
            template: DamageClass | None = None 
            ) -> None: 
        super().__init__(parent)

        self.setWindowTitle("CREATE DAMAGE CLASS")
        self.setMinimumWidth(440)

        current = (
                template.attributes 
                if template is not None 
                else AttributeForm()
                )

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
                "EX: uprooted, heavy branch loss"
                )

        self.failure_mode = _dropdown(FailureMode)

        self.branch_loss = _dropdown(BranchLoss)
        
        self.tree_type = _dropdown(TreeType)
        self.root_plate_visible = _dropdown(Ternary)
        self.crown_intactness = _dropdown(Intactness) 
        self.ground_type = _dropdown(GroundType)
        self.health = _dropdown(Health)
        self.exposure = _dropdown(Exposure)
        self.confidence = _dropdown(Confidence)
        
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(90)

        self.annotator = QLineEdit()

        self.mosaic_id = QLineEdit()
        self.bundle_id = QLineEdit()
        self.tile_id = QLineEdit()

        _select(
                self.failure_mode, 
                current.failure_mode, 
                )  
        _select(
                self.branch_loss, 
                current.branch_loss, 
                )
        _select(
                self.root_plate_visible, 
                current.root_plate_visible
                )
        _select(
                self.crown_intactness, 
                current.intactness
                ) 
        _select( 
                self.ground_type, 
                current.ground_type
                ) 
        _select(
                self.health, 
                current.health_of_tree 
                )
        _select(
                self.tree_type, 
                current.tree_type
                )
        _select(
                self.exposure, 
                current.exposure 
                ) 
        _select(
                self.confidence, 
                current.confidence 
                )

        self.notes.setPlainText(current.notes)

        saved_annotator = str(
                QSettings().value(
                    ANNOTATOR_KEY, 
                    "",
                    )
                or ""
                ) 

        self.annotator.setText(
                current.annotator or saved_annotator 
                )

        if template is not None: 
            self.mosaic_id.setText(template.mosaic_id)
            self.bundle_id.setText(template.bundle_id)
            self.tile_id.setText(template.tile_id)

        self._build_layout()

    def _build_layout(self) -> None: 
        """ create and arrange all widgets in the dialog """

        class_group = QGroupBox("DAMAGE CLASS")
        class_layout = QFormLayout(class_group) 

        class_layout.addRow(
                "class name ", 
                self.name_edit, 
                )

        class_layout.addRow(
                "failure mode", 
                self.failure_mode,
                ) 

        class_layout.addRow(
                "health of tree", 
                self.health
                )
        class_layout.addRow(
                "tree type", 
                self.tree_type
                )
        class_layout.addRow(
                "branchloss class ", 
                self.branch_loss
                ) 


        class_layout.addRow(
                "root plate visible", 
                self.root_plate_visible
                ) 

        class_layout.addRow(
                "ground type", 
                self.ground_type
                )

        class_layout.addRow(
                "crown intactness", 
                self.crown_intactness
                )

        class_layout.addRow(
                "visibility", 
                self.exposure
                ) 

        class_layout.addRow(
                "confidence", 
                self.confidence
                )

        class_layout.addRow(
                "notes", 
                self.notes
                )

        class_layout.addRow(
                "annotator", 
                self.annotator
                )
        
        provenance_group = QGroupBox(
                "provenance"
                )

        provenance_layout = QFormLayout(
                provenance_group
                )
        provenance_layout.addRow(
                "mosaic id",
                self.mosaic_id
                )
        provenance_layout.addRow(
                "bundle id", 
                self.bundle_id
                )

        provenance_layout.addRow(
                "tile id", 
                self.tile_id
                )
        
        buttons = QDialogButtonBox() 
        buttons.addButton(QDialogButtonBox.StandardButton.Save)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        
        layout = QVBoxLayout(self)
        layout.addWidget(class_group)
        layout.addWidget(provenance_group)
        layout.addWidget(buttons)

    def accept(self) -> None: 
        """ validate class name before closing window"""

        if not self.name_edit.text().strip():
            QMessageBox.warning(
                    self, 
                    "TREEANGLE", 
                    "ENTER DAMAGE CLASS NAME"
                    )
            self.name_edit.setFocus()
            return 

        super().accept()
        
    def result_data(self) -> DamageClass:
        """cnvert the completed dialog into a damage class"""

        attributes = AttributeForm(
            annotator=self.annotator.text().strip(),
            branch_loss=self.branch_loss.currentData(),
            confidence=self.confidence.currentData(),
            exposure=self.exposure.currentData(),
            failure_mode=self.failure_mode.currentData(),
            ground_type=self.ground_type.currentData(),
            health_of_tree=self.health.currentData(),
            intactness=self.crown_intactness.currentData(),
            root_plate_visible=(
                self.root_plate_visible.currentData()
            ),
            tree_type=self.tree_type.currentData(),
            notes=self.notes.toPlainText().strip(),
        )

        QSettings().setValue(
            ANNOTATOR_KEY,
            attributes.annotator,
        )

        return DamageClass.create(
            self.name_edit.text().strip(),
            attributes,
            mosaic_id=self.mosaic_id.text().strip(),
            bundle_id=self.bundle_id.text().strip(),
            tile_id=self.tile_id.text().strip(),
        )

def _dropdown(enum_type: type[Enum]) -> QComboBox: 
    """ create dropwdown containing each member of enum"""

    dd = QComboBox()

    for item in enum_type:
        dd.addItem(
                display_name(item), 
                item
                ) 

    return dd 

def _select(drop_down: QComboBox, value: Enum) -> None: 
    """ select an enum value stored in dropdown"""

    index = drop_down.findData(value)

    if index >= 0: 
        drop_down.setCurrentIndex(index)

def _spin(
        minimum: float, 
        maximum: float, 
        step: float, 
        decimals: int, 
        ) -> QDoubleSpinBox: 
    """ create decimal input, can also represent None"""

    spin = QDoubleSpinBox()

    unset_value = minimum - step 
    spin.setRange(
            unset_value, maximum
            )
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setSpecialValueText("not set")
    spin.setValue(unset_value) 
    return spin

def _set_spin(spin: QDoubleSpinBox, value: float | None) -> None: 
    """ load python float or None into an optional spin box """

    if value is None: 
        spin.setValue(spin.minimum())
    else: 
        spin.setValue(value)

def _spin_value(spin: QDoubleSpinBox) -> float | None: 
    """ convert spin value into float or None"""

    if spin.value() == spin.minimum():
        return None 
    return spin.value()





class CaptureTool(QgsMapTool): 
    capture_complete = pyqtSignal(object) 
    
    def __init__(self, canvas) -> None: 
        super().__init__(canvas)

        self._canvas = canvas 
        self._points: list[QgsPointXY] = []

        self._kite = QgsRubberBand(
                canvas, 
                Qgis.GeometryType.Polygon,
                ) 
        self._minor_axis = QgsRubberBand(
                canvas, 
                Qgis.GeometryType.Line,
                )
        self._major_axis = QgsRubberBand(
                canvas, 
                Qgis.GeometryType.Line
                )
        self._vertices = QgsRubberBand(
                canvas, 
                Qgis.GeometryType.Point
                )
        self._kite.setStrokeColor(KITE_STROKE)
        self._kite.setFillColor(KITE_FILL)
        self._kite.setWidth(KITE_WIDTH)
        self._minor_axis.setColor(KITE_MINOR)
        self._minor_axis.setWidth(KITE_WIDTH)
        self._major_axis.setStrokeColor(KITE_MAJOR)
        self._major_axis.setFillColor(KITE_MAJOR)
        self._vertices.setColor(KITE_VERTEX)
        self._vertices.setFillColor(KITE_VERTEX)
        self._vertices.setWidth(1)

        self.setCursor(
                QCursor(Qt.CursorShape.CrossCursor)
                )

    @property
    def points(self) -> tuple[QgsPointXY, ...]:
        return tuple(self._points)

    def canvasPressEvent(self, e: QgsMapMouseEvent | None) -> None:
        """ add, undo, or complete point capture"""
        
        if e is None: 
            return 

        if e.button() == Qt.MouseButton.RightButton:
            self.undo_point()
            return 

        if e.button() != Qt.MouseButton.LeftButton: 
            return 

        point = self.toMapCoordinates(e.pos())
        self._points.append(point)

        if len(self._points) == 4: 
            completed = list(self._points)
            self.reset()
            self.capture_complete.emit(completed)
            return 
        
        self._render()


    def canvasMoveEvent(self, e: QgsMapMouseEvent | None) -> None:
        """ update preview using curent mouse pos"""

        if e is None or not self._points: 
            return 
        cursor = self.toMapCoordinates(e.pos())
        self._render(cursor)

    def keyPressEvent(self, e: QtGui.QKeyEvent | None) -> None:
        """ handles cancelation, point undo """

        if e is None: 
            return 

        if e.key() == Qt.Key.Key_Escape: 
            self.reset()
            e.accept()
            return 
        
        undo = (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
        if e.key() in undo: 
            self.undo_point()
            e.accept()
            return  

        super().keyPressEvent(e)

    def undo_point(self) -> None: 
        """ remove the most recent capture point """
        if self._points: 
            self._points.pop()

        self._render()

    def reset(self) -> None: 
        """ discard and clear preview"""

        self._points.clear()
        self._clear_preview()

    def deactivate(self) -> None: 
        """ clear the preview when another map tool is selected """

        self.reset()
        super().deactivate()

    def clean_up(self) -> None: 
        """ removes all preview graphics when plugin unloads """

        self.reset()
        if self._canvas is None: 
            return  

        scene = self._canvas.scene()

        if scene is None: 
            return 

        for band in self._bands: 
            scene.removeItem(band)


    @property
    def _bands(self) -> tuple[QgsRubberBand, ...]:
        """ return every remporary preview rubber band """

        return (
                self._kite, 
                self._minor_axis, 
                self._major_axis, 
                self._vertices
                )

    def _clear_preview(self) -> None: 
        """ remove all remporary geometries """

        for band in self._bands: 
            band.reset()

    def _render(self, cursor: QgsPointXY | None=None) -> None: 
        """ draw the currently available kite components """
        
        if self._canvas is None: 
            return 

        self._clear_preview()

        preview = list(self._points)

        if cursor is not None and len(preview) < 4: 
            preview.append(cursor)

        if preview: 
            vertex_geometry = QgsGeometry.fromMultiPointXY(preview)

            self._vertices.setToGeometry(vertex_geometry, None)

        if len(preview) >= 2: 
            arrow = _arrow_geometry(
                    preview[0], 
                    preview[1], 
                    self._canvas.mapUnitsPerPixel()
                    )
            self._major_axis.setToGeometry(arrow, None) 

        if len(preview) < 4: 
            return 
    
        
        # kite rendering creation -> move into Kite? 

        base, tip, left, right = preview[:4]

        kite_geometry = QgsGeometry.fromPolygonXY(
                [[base, left, tip, right, base]]
                ) 
        self._kite.setToGeometry(kite_geometry, None)

        minor_axis_geometry = QgsGeometry.fromPolylineXY([left, right])

        self._minor_axis.setToGeometry(minor_axis_geometry, None)

def _arrow_geometry(
        origin: QgsPointXY, 
        end: QgsPointXY, 
        map_unit_per_pixel: float, 
        ) -> QgsGeometry: 
    dx = end.x() - origin.x()
    dy = end.y() - origin.y()

    axis_length = hypot(dx, dy)

    if axis_length <= 0.0: 
        return QgsGeometry.fromPolylineXY([origin, end])

    unit_x = dx / axis_length 
    unit_y = dy / axis_length 

    head_length = min(
            max(
                map_unit_per_pixel * 5.0,
                1.0e-12,
                ), 
            axis_length * 0.35
            )
    head_width = head_length * 0.10 

    head_base_x = end.x() - unit_x * head_length 
    head_base_y = end.y() - unit_y * head_length 

    arrow_left = QgsPointXY(
            head_base_x - unit_y * head_width, 
            head_base_y + unit_x * head_width
            )

    arrow_right = QgsPointXY(
            head_base_x + unit_y * head_width, 
            head_base_y - unit_x * head_width
            )
    
    return QgsGeometry.fromMultiPolylineXY(
            [
                [origin, end], 
                [end, arrow_left], 
                [end, arrow_right]
            ]
        )

class EditTool(QgsMapTool):
    """ select and drag existing control points """
    def __init__(
            self, 
            canvas: QgsMapCanvas, 
            annlayer: QgsVectorLayer, 
            fallvecs: QgsVectorLayer, 
            message_callback: Callable[[str, bool], None],
            ) -> None: 
        super().__init__(canvas)

        self._canvas = canvas 
        self._annlayer = annlayer
        self._fallvecs = fallvecs
        self._message_callback = message_callback 

        self._feature_id: int | None = None 
        self._role_index: int | None = None 

        self._points: list[QgsPointXY] = []
        self._original_points: list[QgsPointXY] = []

        self._preview = QgsRubberBand(
                canvas, 
                Qgis.GeometryType.Polygon
                )

        self._preview.setStrokeColor(
                EDIT_STROKE
                )
        self._preview.setFillColor(
                EDIT_FILL
                )
        self._preview.setWidth(
                EDIT_STROKE_WIDTH
                )
        self.setCursor(
                QCursor(Qt.CursorShape.SizeAllCursor)
                )

    @property 
    def target_layer(self) -> QgsVectorLayer: 
        return self._annlayer 

    @property 
    def fallvec_layer(self) -> QgsVectorLayer: 
        
        return self._fallvecs
         
    def canvasPressEvent(self, e: QgsMapMouseEvent | None) -> None:
        if (
                e is None 
                or e.button() != Qt.MouseButton.LeftButton
            ):
                return 

        hit = self._nearest_role(e.pixelPoint())

        if hit is None: 
            self._message_callback("click closer to control point", False)
            return 
        (self._feature_id, self._role_index, self._points) = hit 
        self._original_points = list(self._points)
        self._annlayer.selectByIds([self._feature_id])

        self.setCursor(QCursor(Qt.CursorShape.DragMoveCursor))

    def canvasMoveEvent(self, e: QgsMapMouseEvent | None) -> None:
        """ move points in preview """

        if (
                e is None 
                or self._feature_id is None 
                or self._role_index is None
                ): 
            return 

        point = self.toLayerCoordinates(self._annlayer, e.pixelPoint())

        self._points[self._role_index] = point 
        self._render()


    def canvasReleaseEvent(self, e: QgsMapMouseEvent | None) -> None:
        """ validate, save edited kite"""

        if (
                e is None 
                or e.button() != Qt.MouseButton.LeftButton 
                or self._feature_id is None 
                or self._role_index is None
            ): 
            return 

        self._points[self._role_index] = (
                self.toLayerCoordinates(
                    self._annlayer, 
                    e.pixelPoint()
                    )
                )
        ann_updated = False 
        original_measurements = None 

        try: 
            measurements = measure(
                    *self._points, 
                    self._annlayer.crs()    
                    )
            original_measurements = measure(
                    *self._original_points, 
                    self._annlayer.crs()
                    )
            update_kite_geometry(
                    self._annlayer, 
                    self._feature_id, 
                    self._points, 
                    measurements
                    ) 
            ann_updated = True
            updated_feature = self._annlayer.getFeature(
                    self._feature_id
                    )

            if not updated_feature.isValid():
                raise RuntimeError(
                        "the updated annotation is no longer available"
                        )

            upsert_fall_vector(
                    self._fallvecs, 
                    updated_feature, 
                    self._points
                    )

        except Exception as error: 
            self._points = list(
                    self._original_points
                    )
            rollback_error: Exception | None = None 

            if (
                    ann_updated 
                    and original_measurements is not None
                ):
                    try:
                        update_kite_geometry(
                                self._annlayer, 
                                self._feature_id, 
                                self._original_points,
                                original_measurements
                                )
                        ann_updated = True
                        restore_feature = (
                                self._annlayer.getFeature(
                                    self._feature_id
                                    )
                                )
                        if restore_feature.isValid():
                            upsert_fall_vector(
                                    self._fallvecs, 
                                    restore_feature, 
                                    self._original_points
                                    )
                    except Exception as rollback: 
                        rollback_error = rollback 
            detail = str(error) 

            if rollback_error is not None: 
                detail += ( 
                           "; rollback also failed: "
                           f"{rollback_error}"
                           )
            self._message_callback(
                    detail, 
                    True,
                    )

        else: 
            self._message_callback(
                    "tree geometry and measurments updated",
                    False,
                )

        finally: 
            self._finish_drag()

    def keyPressEvent(self, e) -> None:
        """ cancel active drag """

        if e is None: 
            return 

        if (
                e.key() == Qt.Key.Key_Escape 
                and self._feature_id is not None 
            ): 
            self._points = list(
                    self._original_points
                    )
            self._finish_drag()
            e.accept()
            return 
        super().keyPressEvent(e)

    def deactivate(self) -> None:
        """ cancel editing when another map tool is selected """
        self._finish_drag()
        super().deactivate() 

    def clean_up(self) -> None: 
      """ remoev the edit preview when plugin unloads """ 

      self._finish_drag()
      scene = self._canvas.scene()

      if scene is not None: 
            scene.removeItem(self._preview)

    def _finish_drag(self) -> None: 
        """ ckear current edit op"""

        self._feature_id = None 
        self._role_index = None 

        self._points = []
        self._original_points = []

        self._preview.reset(
                Qgis.GeometryType.Polygon
                )
        self.setCursor(
                QCursor(Qt.CursorShape.CrossCursor)
                )
        
    def _nearest_role(
            self, 
            screen_position: QPoint, 
            ) -> tuple[int, int, list[QgsPointXY]] | None: 
        """ find nearest stored points"""

        click = self.toLayerCoordinates(
                self._annlayer, 
                screen_position 
                )   
        offset = self.toLayerCoordinates(
                self._annlayer, 
                QPoint(
                    screen_position.x() + 12, 
                    screen_position.y()
                    ), 
                )
        tolerance = max(
                abs(offset.x() - click.x()), 
                abs(offset.y() - click.y()), 
                1.0e-12 
                )

        search_rectangle  = QgsRectangle(
                click.x() - tolerance, 
                click.y() - tolerance, 
                click.x() + tolerance, 
                click.y() + tolerance 
                )

        request = QgsFeatureRequest()
        request.setFilterRect(search_rectangle)
        
        best: (
                tuple[int, int, list[QgsPointXY]] | None
                ) = None 

        best_distance = tolerance 

        iterator = self._annlayer.getFeatures(request)
        feature = QgsFeature()

        try:
            while iterator.nextFeature(feature):
                points = points_from(feature)

                for role_index, point in enumerate(points):
                    dx = point.x() - click.x()
                    dy = point.y() - click.y()
                    distance = hypot(dx, dy)

                    if distance <= best_distance:
                        best_distance = distance
                        best = (
                            int(feature.id()),
                            role_index,
                            points,
                        )

                feature = QgsFeature()
        finally:
            iterator.close()
        return best 

    def _render(self) -> None: 
        """ draws edited kite without saving """

        if len(self._points) != 4: 
            return 

        map_points = [
                self.toMapCoordinates(
                    self._annlayer, 
                    point   
                    )
                for point in self._points   
                ]

        base, tip, left, right = map_points 
        geometry = QgsGeometry.fromPolygonXY(
                [[base, left, tip, right, base]]
                )
        self._preview.setToGeometry(
                geometry, 
                None    
                )
