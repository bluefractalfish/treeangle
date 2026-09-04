"""
    PUBLIC: TreeAnglePlugin(iface)

    treeangle plugin controller for joining lower level logic with QGIS 

"""

from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QStandardPaths, Qt
from qgis.PyQt.QtGui import QIcon

try:
    # Qt 6
    from qgis.PyQt.QtGui import QAction
except ImportError:
    # Qt 5
    from qgis.PyQt.QtWidgets import QAction

from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox
)

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .damage_classes import (
    DamageClass,
    DamageClassStore,
)
from .frontend import (
    CaptureTool,
    EditTool,
    FormDialog,
    TreeDock
)
from .geometry import measure
from .layers import (
    RasterSource,
    add_feature_now,
    create_kite_feature,
    delete_feature_now,
    fall_vector_path,
    is_fall_vector,
    is_kite,
    layer_storage_path,
    open_fall_vectors,
    open_layer,
    source_from,
    synchronize,
    upsert_fall_vector,
)

def _exec_dialog(dialog: QDialog) -> bool:
    """Run a dialog under either Qt 5 or Qt 6."""

    execute = getattr(dialog, "exec", None)

    if execute is None:
        execute = dialog.exec_

    return bool(execute())

class TreeAnglePlugin:

    """ treeangle plugin interface """
    def __init__(self, iface) -> None: 
        self.iface = iface 
        self.tree_count_label = None 
        self._watched_annotation_layer = None
        self.canvas = iface.mapCanvas()
        self.toolbar = None 
        self.actions: list[QAction] = [] 
        # layers 
        self.annotation_layer = None 
        self.fall_vector_layer = None 
        # tools 
        self.capture_tool = None 
        self.edit_tool = None 
        self.history_dock: TreeDock | None = None 
        self._watched_annotation_layer: QgsVectorLayer | None = None 
        #actions 
        self.create_action: QAction | None = None 
        self.create_class_action: QAction | None = None 
        self.capture_action: QAction | None = None
        self.edit_action: QAction | None = None 
        
        self.edit_class_action = None 
        self.delete_class_action = None 

        self.damage_dropdown: QComboBox | None=None 
        self.damage_store = DamageClassStore()

    def initGui(self) -> None: 

        # create toolbar 
        self.toolbar = self.iface.addToolBar("TreeAngle")
        self.toolbar.setObjectName("TreeAngleTools")

        # add actions  
        #=============================================#
        # open or create GPKG to store annotations
        self.initGPKGCreation()
        # open or create damage class to store
        # damage attributes across multiple annotations 
        self.initDamageClasses()
        # create new kites for annotated trees 
        self.initAnnotator() 
        # open editer to change existing points 
        self.initEditor() 
        #=============================================#
        
        self.tree_count_label = QLabel(
            "trees: 0",
            self.toolbar,
        )
        self.tree_count_label.setContentsMargins(
            8,
            0,
            3,
            0,
        )
        self.toolbar.addWidget(
            self.tree_count_label
        )
        
        self.history_dock = TreeDock(
            self.iface.mainWindow()
        )

        self.iface.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.history_dock,
        )

        history_action = self.history_dock.toggleViewAction()
        history_action.setText("HISTORY")

        self.toolbar.addAction(history_action)
        self.history_dock.show()

    def initAnnotator(self) -> None: 
        self.capture_action = self._create_action(
                "ANNOTATE", 
                self.activate_capture, 
                checkable=True
                )
    def initGPKGCreation(self) -> None: 
        self.create_action = self._create_action(
                "OPEN GPKG", 
                self.create_annotation_layer
                )
    def initDamageClasses(self) -> None: 
        self.create_class_action = self._create_action(
                "DAMAGE CLASS", 
                self.create_damage_class 
                ) 
        if self.toolbar is None: 
            return 
        class_label = QLabel("damage_class:")
        class_label.setContentsMargins(3,0,2,0)
        self.toolbar.addWidget(class_label)
        self.damage_dropdown = QComboBox(self.toolbar)
        self.damage_dropdown.setMinimumContentsLength(18)
        self.damage_dropdown.setToolTip(
                "damage class values copied to each new tree_kite"
                )
        self.damage_dropdown.currentIndexChanged.connect(
                self._damage_class_selected
                )
        self.toolbar.addWidget(self.damage_dropdown)
        self._refresh_damage_dropdown() 

        self.edit_class_action = self._create_action(
                "EDIT CLASS", 
                self.edit_damage_class,
                ) 
        self.delete_class_action = self._create_action(
                "DELETE CLASS", 
                self.delete_damage_class
                )

    def initEditor(self) -> None: 
        self.edit_action = self._create_action(
                "EDIT POINTS", 
                self.activate_edit, 
                checkable=True 
                )  

    def unload(self) -> None: 
        """ removes menu items """

        if (
                self.capture_tool 
                and self.canvas.mapTool()
                is self.capture_tool
                ): self.canvas.unsetMapTool(self.capture_tool)

        if (
                self.edit_tool 
         and self.canvas.mapTool()
                is self.edit_tool
                ): self.canvas.unsetMapTool(self.edit_tool )

        if self.capture_tool: 
            self.capture_tool.clean_up()

        if self.edit_tool: 
            self.edit_tool.clean_up()
        
        for action in self.actions: 
            self.iface.removePluginVectorMenu("&TreeAngle", action)
            if self.toolbar: 
                self.toolbar.removeAction(action)

        if self.toolbar: 
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater() 
            self.toolbar = None 

        if self.history_dock is not None:
            self.iface.removeDockWidget(
                self.history_dock
                )

            self.history_dock.deleteLater()
            self.history_dock = None
            self.actions.clear()

        watched = self._watched_annotation_layer

        if watched is not None:
            try:
                watched.featureAdded.disconnect(
                    self._feature_count_changed
                )
            except (TypeError, RuntimeError):
                pass

            try:
                watched.featureDeleted.disconnect(
                    self._annotation_feature_deleted
                )
            except (TypeError, RuntimeError):
                pass

        self._watched_annotation_layer = None

    def _create_action(
        self,
        text,
        callback,
        *,
        checkable=False,
        icon=None,
    ):
        action = QAction(
            text,
            self.iface.mainWindow(),
        )
        action.setCheckable(checkable)
        action.triggered.connect(callback)

        self.iface.addPluginToVectorMenu(
            "&TreeAngle",
            action,
        )

        if self.toolbar is not None:
            self.toolbar.addAction(action)

        self.actions.append(action)
        return action
    
    def create_damage_class(
        self,
        _checked: bool = False,
    ) -> DamageClass | None:
        dialog = FormDialog(
            self.iface.mainWindow(),
            template=self.damage_store.active_class,
        )

        if not _exec_dialog(dialog):
            return None

        damage_class = dialog.result_data()

        try:
            self.damage_store.add(
                damage_class,
                make_active=True,
            )
        except ValueError as error:
            self._message(
                str(error),
                error=True,
            )
            return None

        self._refresh_damage_dropdown()
        self._message(f"damage_class: {damage_class.name} is now active ")
        return damage_class 
    def edit_damage_class(
            self, 
            _checked: bool = False, 
            ) -> DamageClass | None: 
        active = self.damage_store.active_class 
        if active is None: 
            self._message(
                    "select a damage class to edit",
                    error = True 
                    )  
            return None 
        dialog = FormDialog(
                self.iface.mainWindow(), 
                template=active, 
                edit_existing=True, 
                ) 
        if not _exec_dialog(dialog): 
            return None 

        edited_class = dialog.result_data()

        try: 
            self.damage_store.update(edited_class)
        except ValueError as error: 
            self._message(
                    str(error), 
                    error=True
                    )
            return None 

        self._refresh_damage_dropdown() 

        self._message(
                f"damage class `{edited_class.name}` updated"
                "existing tree annotations were not changed"
                )
        return edited_class 
    
    def delete_damage_class(
            self, 
            _checked: bool = False
            ) -> None: 
        active = self.damage_store.active_class 

        if active is None: 
            self._message(
                    "select a damage class to delete", 
                    error=True
                    )
            return 

        standard_button = getattr(
                QMessageBox, 
                "StandardButton", 
                QMessageBox
                )
        confirmation = QMessageBox(
                self.iface.mainWindow()
                )
        confirmation.setWindowTitle(
                "DELETE DAMAGE CLASS "
                )
        confirmation.setText(
                f"delete damage class `{active.name}`?"
                )
        confirmation.setInformativeText(
                "existing tree annotations will keep their copied "
                "damage values"
                )
        delete_button = confirmation.addButton(
                standard_button.Yes
                )

        confirmation.addButton(
                standard_button.Cancel
                ) 

        _exec_dialog(confirmation)

        if confirmation.clickedButton() is not delete_button: 
            return 

        try: 
            self.damage_store.delete(
                    active.class_id
                    )
        except ValueError as error: 
            self._message(
                    str(error), 
                    error=True
                    ) 
            return 

        self._refresh_damage_dropdown() 
        self._message(
                f"damage class `{active.name}` deleted "
                "existing annotations were not changed"
                )
        
    def _refresh_damage_dropdown(self) -> None: 
        dropdown = self.damage_dropdown 
        if dropdown is None: 
            return 

        dropdown.blockSignals(True)
        dropdown.clear()

        if not self.damage_store.classes: 
            dropdown.addItem("none selected", "")

        else: 
            for damage_class in self.damage_store.classes: 
                # add each existing class to dropdown 
                dropdown.addItem(damage_class.name, damage_class.class_id)

            active = self.damage_store.active_class 
            if active: 
                index = dropdown.findData(active.class_id)
                if index >= 0: 
                    dropdown.setCurrentIndex(index)

        dropdown.blockSignals(False)
    
    def _damage_class_selected(self, index: int) -> None: 

        if self.damage_dropdown is None or index < 0:
            return 

        class_id = str(self.damage_dropdown.itemData(index) or "")
        try: 
            self.damage_store.set_active(class_id)
        except ValueError as e: 
            self._message(str(e), error=True)
            self._refresh_damage_dropdown()
            return 
        active = self.damage_store.active_class 
        if active: 
            self._message(f"damage_class: `{active.name}` is active")


    def create_annotation_layer(self, _checked: bool = False) -> QgsVectorLayer | None: 
        raster = self._choose_reference_raster()
        if raster is None: 
            self._message("load or select raster first", error=True)
            return None 
        
        def_dir = self._default_output_directory()
        path, _ = QFileDialog.getSaveFileName(
                self.iface.mainWindow(), 
                "create or open TreeAngle gpkg ", 
                str(def_dir / "fallen_kites_01.gpkg"),
                "GeoPackage (*.gpkg)",
                )
        if not path: 
            return None 
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"

        source = RasterSource(
                layer_id=raster.id(), 
                name=raster.name(), 
                uri=raster.source(), 
                crs_authid=raster.crs().authid(), 
                )
        vector_path = fall_vector_path(path)
        try: 
            layer = open_layer(path, raster.crs(), source)
            vector_layer = open_fall_vectors(vector_path, raster.crs())
            synchronize(layer,vector_layer)
        except (ValueError, RuntimeError) as error: 
            self._message(str(error), error=True)
            return None 

        project = QgsProject.instance()
        if project.mapLayer(layer.id()) is None: 
            project.addMapLayer(layer)
        if project.mapLayer(vector_layer.id()) is None: 
            project.addMapLayer(vector_layer)

        layer.setCustomProperty(
                "treeangle/fall_vector_path", 
                str(vector_path)
                )
        vector_layer.setCustomProperty(
                "treeangle/annotation_path", 
                str(Path(path).expanduser().resolve()), 
                )

        self._set_annotation_layer(layer)
        self.fall_vector_layer = vector_layer 
        self.iface.setActiveLayer(layer)
            
        return layer 

    def activate_capture(
        self,
        checked: bool = True,
    ) -> None:
        if not checked:
            if (
                self.capture_tool is not None
                and self.canvas.mapTool()
                is self.capture_tool
            ):
                self.canvas.unsetMapTool(
                    self.capture_tool
                )
            return

        if self.damage_store.active_class is None:
            if self.create_damage_class() is None:
                self._set_checked(
                    self.capture_action,
                    False,
                )
                return

        layer = (
            self._current_annotation_layer()
            or self.create_annotation_layer()
        )

        if layer is None:
            self._set_checked(
                self.capture_action,
                False,
            )
            return

        self._set_annotation_layer(layer)

        try:
            self.fall_vector_layer = (
                self._ensure_fall_vector_layer(layer)
            )
        except (ValueError, RuntimeError) as error:
            self._set_checked(
                self.capture_action,
                False,
            )
            self._message(
                str(error),
                error=True,
            )
            return

        if self.capture_tool is None:
            self.capture_tool = CaptureTool(
                self.canvas
            )
            self.capture_tool.capture_complete.connect(
                self._finish_capture
            )

            if self.capture_action is not None:
                self.capture_tool.setAction(
                    self.capture_action
                )

        # This must be outside the creation block.
        self.canvas.setMapTool(
            self.capture_tool
        )
        self._set_checked(
            self.capture_action,
            True,
        )
        self._set_checked(
            self.edit_action,
            False,
        )

        active = self.damage_store.active_class
        class_name = (
            active.name
            if active is not None
            else "none"
        )

        self._message(
                f"damage_class `{class_name}` "
                "inscribe fallen tree in kite. "
                )

    def activate_edit(self, checked: bool = True) -> None: 
        if not checked and self.edit_tool is not None: 
            if self.canvas.mapTool() is self.edit_tool: 
                self.canvas.unsetMapTool(self.edit_tool)
            return 

        layer = self._current_annotation_layer()
        if layer is None: 
            self._set_checked(self.edit_action, False)
            self._message("select layer first", error=True)
            return 
        
        self._set_annotation_layer(layer)
        try: 
            vector_layer = self._ensure_fall_vector_layer(layer)

        except (ValueError, RuntimeError) as error:
            self._set_checked(self.edit_action, False)
            self._message(str(error), error=True)
            return

        if self.edit_tool is not None and (
            self.edit_tool.target_layer is not layer
            or self.edit_tool.fallvec_layer is not vector_layer
        ):
            self.edit_tool.clean_up()
            self.edit_tool = None
        if self.edit_tool is None:
            self.edit_tool = EditTool(
                self.canvas,
                layer,
                vector_layer,
                self._message,
            )
            self.edit_tool.edit_complete.connect(
                    self._refresh_tree_history
                    )
            if self.edit_action:
                self.edit_tool.setAction(self.edit_action)

        self.canvas.setMapTool(self.edit_tool)
        self._set_checked(self.edit_action, True)
        self._set_checked(self.capture_action, False)
        self._message("drag any point to new location.")
    
    def _finish_capture(self, map_points) -> None: 
        layer = self.annotation_layer 
        active_class = self.damage_store.active_class 
        if layer is None or not is_kite(layer): 
            self._message("layer no longer available.", error=True)
            return 
        if active_class is None: 
            self._message("select or create damage class before annotating.", error=True)
            return 
        if len(map_points) != 4: 
            self._message("four capture points are expected.", error=True)
            return 
        
        try: 
            vector_layer = self._ensure_fall_vector_layer(layer)
            transform = QgsCoordinateTransform(
                    self.canvas.mapSettings().destinationCrs(), 
                    layer.crs(), 
                    QgsProject.instance().transformContext(), 
                    )
            layer_points = [transform.transform(point) for point in map_points]
            metrics = measure(*layer_points, layer.crs())
        except Exception as error: 
            self._message(str(error), error=True)
            return 

        feature = create_kite_feature(
                layer, 
                layer_points, 
                metrics, 
                active_class, 
                source_from(layer)
                )
        feature_id = None 
        try: 
            feature_id = add_feature_now(layer, feature)
            upsert_fall_vector(vector_layer, feature, layer_points)
        except(ValueError, RuntimeError) as e:
            rollback_error = None 
            if feature_id is not None: 
                try: 
                    delete_feature_now(layer, feature_id)
                except RuntimeError as rollback: 
                    rollback_error = rollback 
            detail = str(e)
            if rollback_error is not None: 
                detail += f"; rollback also failed: {rollback_error}"
            self._message(detail, error=True)
            return  

        layer.selectByIds([feature_id])
        self._message(f"saved `{active_class.name}`")
        self._refresh_tree_count()
        self._refresh_tree_history()

    def _ensure_fall_vector_layer(
        self,
        annotation_layer: QgsVectorLayer,
    ) -> QgsVectorLayer:
        vector_path = fall_vector_path(annotation_layer)
        current = self.fall_vector_layer
        if is_fall_vector(current):
            try:
                if layer_storage_path(current) == vector_path:
                    return current
            except ValueError:
                pass

        vector_layer = None
        for candidate in QgsProject.instance().mapLayers().values():
            if not is_fall_vector(candidate):
                continue
            try:
                if layer_storage_path(candidate) == vector_path:
                    vector_layer = candidate
                    break
            except ValueError:
                continue
        if vector_layer is None:
            vector_layer = open_fall_vectors(
                vector_path,
                annotation_layer.crs(),
            )
            QgsProject.instance().addMapLayer(vector_layer)

        synchronize(annotation_layer, vector_layer)
        annotation_layer.setCustomProperty("treeangle/fall_vector_path", str(vector_path))
        vector_layer.setCustomProperty(
            "treeangle/annotation_path",
            str(layer_storage_path(annotation_layer)),
        )
        self.fall_vector_layer = vector_layer
        return vector_layer

    def _current_annotation_layer(self) -> QgsVectorLayer | None:
        active = self.iface.activeLayer()
        if is_kite(active):
            return active
        if is_kite(self.annotation_layer):
            return self.annotation_layer

        candidates = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if is_kite(layer)
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = [layer.name() for layer in candidates]
            name, accepted = QInputDialog.getItem(
                self.iface.mainWindow(),
                "choose layer",
                "annotate layer:",
                names,
                0,
                False,
            )
            if accepted:
                return candidates[names.index(name)]
        return None

    def _choose_reference_raster(self) -> QgsRasterLayer | None:
        active = self.iface.activeLayer()
        if isinstance(active, QgsRasterLayer) and active.isValid():
            return active
        rasters = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsRasterLayer) and layer.isValid()
        ]
        if len(rasters) == 1:
            return rasters[0]
        if len(rasters) > 1:
            names = [layer.name() for layer in rasters]
            name, accepted = QInputDialog.getItem(
                self.iface.mainWindow(),
                "choose reference raster",
                "raster CRS and provenance source:",
                names,
                0,
                False,
            )
            if accepted:
                return rasters[names.index(name)]
        return None

    @staticmethod
    def _default_output_directory() -> Path:
        project_path = QgsProject.instance().fileName()
        if project_path:
            return Path(project_path).resolve().parent
        location_type = getattr(
                QStandardPaths, 
                "StandardLocation", 
                QStandardPaths,
                )
        documents = QStandardPaths.writableLocation(
            location_type.DocumentsLocation
        )
        return Path(documents)

    @staticmethod
    def _set_checked(action: QAction | None, checked: bool) -> None:
        if action is not None:
            action.setChecked(checked)

    def _message(self, text: str, error: bool = False) -> None:
        self.iface.messageBar().pushMessage(
            "TreeAngle",
            text,
            level=(
                Qgis.MessageLevel.Critical
                if error
                else Qgis.MessageLevel.Info
            ),
            duration=8 if error else 5,
        )

    def _set_annotation_layer(
        self,
        layer: QgsVectorLayer,
    ) -> None:
        previous = self._watched_annotation_layer

        if previous is not None and previous is not layer:
            try:
                previous.featureAdded.disconnect(
                    self._feature_count_changed
                )
            except (TypeError, RuntimeError):
                pass

            try:
                previous.featureDeleted.disconnect(
                    self._annotation_feature_deleted
                )
            except (TypeError, RuntimeError):
                pass

        self.annotation_layer = layer 

        if previous is not layer:
            layer.featureAdded.connect(
                self._feature_count_changed
            )
            layer.featureDeleted.connect(
                self._annotation_feature_deleted
            )
            self._watched_annotation_layer = layer

        self._refresh_tree_count()

        if self.history_dock is not None: 
            self.history_dock.set_layer(layer)


    def _feature_count_changed(
        self,
        _feature_id=None,
    ) -> None:
        self._refresh_tree_count()
        self._refresh_tree_history()
    
    def _feature_values_changed(
            self, 
            *_args, 
            ) -> None: 
        self._refresh_tree_history()


    def _annotation_feature_deleted(
        self,
        _feature_id=None,
    ) -> None:
        self._refresh_tree_count()
        self._refresh_tree_history()
        layer = self.annotation_layer
        vector_layer = self.fall_vector_layer

        if (
            layer is None
            or vector_layer is None
            or not is_kite(layer)
            or not is_fall_vector(vector_layer)
        ):
            return

        try:
            synchronize(
                layer,
                vector_layer,
            )
        except (ValueError, RuntimeError) as error:
            self._message(
                "tree was deleted, but fall-vector "
                f"synchronization failed: {error}",
                error=True,
            )


    def _refresh_tree_count(self) -> None:
        if self.tree_count_label is None:
            return

        layer = self.annotation_layer

        count = (
            max(0, int(layer.featureCount()))
            if layer is not None and is_kite(layer)
            else 0
        )

        self.tree_count_label.setText(
            f"(trees: {count})"
        ) 

    def _refresh_tree_history(
        self,
        *_unused,
    ) -> None:
        if self.history_dock is not None:
            self.history_dock.refresh()
