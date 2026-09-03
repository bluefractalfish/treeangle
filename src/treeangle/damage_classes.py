
""" 

categorical values for TreeAngle attributes, 
damageclasses, and damage class storeage

"""

from __future__ import annotations 

from dataclasses import  dataclass, field 
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4 
import json 
from qgis.core import QgsSettings 



class StringEnum(str, Enum):
    @classmethod 
    def values(cls) -> tuple[str, ...]: 
        return tuple(member.value for member in cls)


class BranchLoss(StringEnum):
    """ what percent of branch loss occurred """
    NONE = "none" # lacking any branch loss 
    LOW = "0_25" # less than 25 percent branch loss 
    MEDIUM = "25_50" # between 25 and 50 percent branch loss
    HIGH = "50_75" # between 50 and 75 percent branch loss 
    SEVERE = "75_100" # between 75 pct and complete branch loss 
    UNKNOWN = "unknown"  
    
 
class Confidence(StringEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def display_name(value: Enum) -> str: 
    if isinstance(value, BranchLoss): 
        labels = {
            "none": "none",
            "0_25": "<25%",
            "25_50": "25-50%",
            "50_75": "50-75%",
            "75_100": ">75%",
            "unknown": "unknown"
            }
        return labels.get(
                value.value, 
                value.value.replace("_", "").title()
                )

    return value.value.replace("_","").title()

class Exposure(StringEnum):
    CLEAR = "clear"
    PARTIAL = "partial" 
    LOW = "low"

class FailureMode(StringEnum):
    """ how did the trunk fail? """
    UPROOTED = "uprooted"
    BASAL_SNAP = "basal_snap"
    MID_TRUNK_SNAP = "mid_trunk_snap"
    UPPER_TRUNK_SNAP = "upper_trunk_snap"
    CROWN_BREAK = "crown_break"
    THROWN = "thrown"
    MANUALLY_MOVED = "manually_moved"
    UNKNOWN = "unknown"

class GroundType(StringEnum):
    """ what kind of ground, best guess """
    UNKNOWN = "unknown"
    DIRT = "dirt"
    GRASS = "grass"
    CROPS = "crops"
    DEBRIS = "debris"
    FOREST = "forest"

class Health(StringEnum):
    """ how healthy does the tree look """
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    FAIR = "fair"
    UNHEALTHY = "unhealthy"
    DEAD = "dead"

class Intactness(StringEnum):
    """ level of crown damage """
    INTACT = "intact"
    MOSTLY_INTACT = "mostly_intact"
    FRAGMENTED = "fragmented"
    ABSENT = "absent"
    UNKNOWN = "unknown"

class Ternary(StringEnum):
    # yes | no | unknown
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

class TreeType(StringEnum): 
    """ rough type of tree """
    CONIFER = "conifer"
    DECIDUOUS = "deciduous"
    SHRUB = "shrub"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class AttributeForm:
    annotator: str = ""
    branch_loss: BranchLoss = BranchLoss.NONE 
    confidence: Confidence = Confidence.MEDIUM 
    exposure: Exposure = Exposure.CLEAR
    failure_mode: FailureMode = FailureMode.UNKNOWN 
    ground_type: GroundType = GroundType.UNKNOWN 
    health_of_tree: Health = Health.FAIR 
    intactness: Intactness = Intactness.UNKNOWN  
    notes: str = ""
    root_plate_visible: Ternary = Ternary.UNKNOWN
    tree_type: TreeType = TreeType.UNKNOWN


    @classmethod
    def from_storage_dict(
        cls,
        values: Mapping[str, Any],
    ) -> "AttributeForm":
        return cls(
            annotator=str(values.get("annotator", "") or ""),
            branch_loss=BranchLoss(
                values.get(
                    "branch_loss_class",
                    BranchLoss.NONE.value,
                )
            ),
            confidence=Confidence(
                values.get(
                    "confidence",
                    Confidence.MEDIUM.value,
                )
            ),
            exposure=Exposure(
                values.get(
                    "exposure",
                    Exposure.CLEAR.value,
                )
            ),
            failure_mode=FailureMode(
                values.get(
                    "failure_mode",
                    FailureMode.UNKNOWN.value,
                )
            ),
            ground_type=GroundType(
                values.get(
                    "ground_type",
                    GroundType.UNKNOWN.value,
                )
            ),
            health_of_tree=Health(
                values.get(
                    "health_of_tree",
                    Health.FAIR.value,
                )
            ),
            intactness=Intactness(
                values.get(
                    "crown_intactness",
                    values.get(
                        "intactness_of_crown",
                        Intactness.UNKNOWN.value,
                    ),
                )
            ),
            notes=str(values.get("notes", "") or ""),
            root_plate_visible=Ternary(
                values.get(
                    "root_plate_visible",
                    Ternary.UNKNOWN.value,
                )
            ),
            tree_type=TreeType(
                values.get(
                    "tree_type",
                    TreeType.UNKNOWN.value,
                )
            ),
        ) 

    def as_storage_dict(self) -> dict[str, object]:
        return {
            "annotator": self.annotator,
            "branch_loss_class": self.branch_loss.value,
            "confidence": self.confidence.value,
            "exposure": self.exposure.value,
            "failure_mode": self.failure_mode.value,
            "ground_type": self.ground_type.value,
            "health_of_tree": self.health_of_tree.value, 
            "crown_intactness": self.intactness.value,
            "root_plate_visible": self.root_plate_visible.value,
            "tree_type": self.tree_type.value,
            "notes": self.notes,
        }

@dataclass(frozen=True, slots=True)
class DamageClass:
    """ named damage template selected once and copied to many trees."""

    class_id: str
    name: str
    attributes: AttributeForm = field(default_factory=AttributeForm)
    mosaic_id: str = ""
    bundle_id: str = ""
    tile_id: str = ""

    def __post_init__(self) -> None:
        clean_name = self.name.strip()
        if not clean_name:
            raise ValueError("A damage class needs a name.")
        if len(clean_name) > 100:
            raise ValueError("A damage class name cannot exceed 100 characters.")

        object.__setattr__(self, "name", clean_name)

    @classmethod
    def create(
        cls,
        name: str,
        attributes: AttributeForm,
        *,
        class_id: str | None = None, 
        mosaic_id: str = "",
        bundle_id: str = "",
        tile_id: str = "",
    ) -> "DamageClass":
        return cls(
            class_id=class_id or str(uuid4()),
            name=name.strip(),
            attributes=attributes,
            mosaic_id=mosaic_id.strip(),
            bundle_id=bundle_id.strip(),
            tile_id=tile_id.strip(),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "class_id": self.class_id,
            "name": self.name,
            "attributes": self.attributes.as_storage_dict(),
            "mosaic_id": self.mosaic_id,
            "bundle_id": self.bundle_id,
            "tile_id": self.tile_id,
        }

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "DamageClass":
        attributes = values.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError("Damage class attributes must be an object.")
        return cls(
            class_id=str(values["class_id"]),
            name=str(values["name"]),
            attributes=AttributeForm.from_storage_dict(attributes),
            mosaic_id=str(values.get("mosaic_id", "") or ""),
            bundle_id=str(values.get("bundle_id", "") or ""),
            tile_id=str(values.get("tile_id", "") or ""),
        )



""" 


persitence for named damage classes: load, save, and set templates

translate between domain obj and QGIS settings

"""



CLASSES_KEY = "treeangle/damage_classes"
ACTIVE_CLASS_KEY = "treeangle/active_damage_class_id"


class DamageClassStore: 
    """ load, save, select reusable damage class templates """

    def __init__(self, settings: QgsSettings | None = None) -> None: 
        self._settings = settings or QgsSettings()
        self._classes = self._load()
        self._active_id = str(self._settings.value(ACTIVE_CLASS_KEY, "") or "")

        if self._active_id not in {item.class_id for item in self._classes}: 
            self._active_id = self._classes[0].class_id if self._classes else ""


    @property 
    def classes(self) -> tuple[DamageClass, ...]:
        return tuple(self._classes)

    @property 
    def active_class(self) -> DamageClass | None:
        return next(
                (
                    item 
                    for item in self._classes 
                    if item.class_id == self._active_id
                    ), 
                    None,
                ) 

    def add(
        self,
        damage_class: DamageClass,
        *,
        make_active: bool = True,
    ) -> None:
        normalized_name = damage_class.name.casefold()

        if any(
            item.name.casefold() == normalized_name
            for item in self._classes
        ):
            raise ValueError(
                f'a damage class named "{damage_class.name}" '
                "already exists."
            )

        self._classes.append(damage_class)

        if make_active:
            self._active_id = damage_class.class_id

        self._save()


    def update(
        self,
        damage_class: DamageClass,
    ) -> None:
        """replace an existing class while preserving its UUID"""

        matching_index = None

        for index, existing in enumerate(self._classes):
            if existing.class_id == damage_class.class_id:
                matching_index = index
                break

        if matching_index is None:
            raise ValueError(
                "the damage class being edited no longer exists"
            )

        normalized_name = damage_class.name.casefold()

        duplicate_exists = any(
            existing.class_id != damage_class.class_id
            and existing.name.casefold() == normalized_name
            for existing in self._classes
        )

        if duplicate_exists:
            raise ValueError(
                f'a damage class named "{damage_class.name}" '
                "already exists."
            )

        self._classes[matching_index] = damage_class
        self._active_id = damage_class.class_id
        self._save()


    def delete(self, class_id: str) -> None:
        """delete a saved template, not existing tree attributes"""

        matching_index = None

        for index, existing in enumerate(self._classes):
            if existing.class_id == class_id:
                matching_index = index
                break

        if matching_index is None:
            raise ValueError(
                "The damage class no longer exists."
            )

        self._classes.pop(matching_index)

        if self._active_id == class_id:
            if self._classes:
                next_index = min(
                    matching_index,
                    len(self._classes) - 1,
                )
                self._active_id = (
                    self._classes[next_index].class_id
                )
            else:
                self._active_id = ""

        self._save()


    def set_active(self, class_id: str) -> None:
        known_ids = {
            item.class_id
            for item in self._classes
        }

        if class_id and class_id not in known_ids:
            raise ValueError(
                "The selected damage class no longer exists."
            )

        self._active_id = class_id

        self._settings.setValue(
            ACTIVE_CLASS_KEY,
            self._active_id,
        )
    def _load(self) -> list[DamageClass]: 
        raw = self._settings.value(CLASSES_KEY, "[]")
        try: 
            decoded = json.loads(str(raw))
        except (TypeError, ValueError): 
            return []

        if not isinstance(decoded, list): 
            return []

        classes: list[DamageClass] = []
        for item in decoded: 
            if not isinstance(item, dict): 
                continue 
            try:
                classes.append(DamageClass.from_dict(item))
            except (KeyError, TypeError, ValueError): 
                continue 
        return classes 

    def _save(self) -> None: 
        payload = [item.as_dict() for item in self._classes]
        self._settings.setValue(CLASSES_KEY, json.dumps(payload, separators=(",",":"))) 
        self._settings.setValue(ACTIVE_CLASS_KEY, self._active_id)
