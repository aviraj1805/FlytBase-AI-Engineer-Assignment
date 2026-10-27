"""Vocabulary for turning free-text frame captions into structured detections.

Generated with Claude Code from the feature spec, then hand-tuned against the demo
scenario. Everything is lower-case; multi-word phrases are matched before single words.
"""
from __future__ import annotations

COLORS: dict[str, str] = {
    "black": "black", "white": "white", "silver": "silver", "grey": "gray", "gray": "gray",
    "red": "red", "maroon": "red", "blue": "blue", "navy": "blue", "green": "green",
    "yellow": "yellow", "orange": "orange", "brown": "brown", "beige": "beige", "tan": "beige",
    "dark": "dark", "purple": "purple", "pink": "pink", "gold": "gold",
}

# (make, model) -> vehicle type. Model regexes tolerate "F150", "F-150", "f 150".
VEHICLE_MODELS: list[tuple[str, str, str, str]] = [
    # (regex, make, model, type)
    (r"f[- ]?150", "Ford", "F150", "truck"),
    (r"f[- ]?250", "Ford", "F250", "truck"),
    (r"transit", "Ford", "Transit", "van"),
    (r"silverado", "Chevrolet", "Silverado", "truck"),
    (r"tacoma", "Toyota", "Tacoma", "truck"),
    (r"camry", "Toyota", "Camry", "car"),
    (r"corolla", "Toyota", "Corolla", "car"),
    (r"civic", "Honda", "Civic", "car"),
    (r"cr[- ]?v", "Honda", "CR-V", "suv"),
    (r"model [3sxy]", "Tesla", None, "car"),
    (r"sprinter", "Mercedes", "Sprinter", "van"),
    (r"wrangler", "Jeep", "Wrangler", "suv"),
]

MAKES: dict[str, str] = {
    "ford": "Ford", "toyota": "Toyota", "honda": "Honda", "tesla": "Tesla", "chevrolet": "Chevrolet",
    "chevy": "Chevrolet", "bmw": "BMW", "audi": "Audi", "mercedes": "Mercedes", "jeep": "Jeep",
    "hyundai": "Hyundai", "kia": "Kia", "nissan": "Nissan", "ram": "Ram", "mahindra": "Mahindra",
    "tata": "Tata", "suzuki": "Suzuki",
}

VEHICLE_TYPES: dict[str, str] = {
    "pickup truck": "truck", "pick-up truck": "truck", "pickup": "truck", "truck": "truck", "lorry": "truck",
    "box truck": "truck", "delivery van": "van", "minivan": "van", "van": "van", "suv": "suv",
    "sedan": "car", "hatchback": "car", "coupe": "car", "car": "car", "vehicle": "car",
    "motorcycle": "motorcycle", "motorbike": "motorcycle", "scooter": "motorcycle",
    "bicycle": "bicycle", "cyclist": "bicycle", "bus": "bus", "forklift": "forklift",
}

PERSON_WORDS: dict[str, int] = {
    # word -> implied count
    "person": 1, "man": 1, "woman": 1, "individual": 1, "figure": 1, "pedestrian": 1,
    "intruder": 1, "worker": 1, "courier": 1, "delivery driver": 1, "guard": 1, "visitor": 1,
    "child": 1, "kid": 1, "someone": 1, "jogger": 1, "people": 2, "persons": 2, "men": 2,
    "workers": 2, "driver": 1, "gardener": 1, "cleaner": 1, "technician": 1, "staff": 1,
}

NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "a couple of": 2, "several": 3,
    "a group of": 3, "group of": 3, "a crowd of": 5,
}

ANIMALS: dict[str, str] = {
    "dog": "dog", "cat": "cat", "deer": "deer", "fox": "fox", "raccoon": "raccoon",
    "coyote": "coyote", "cow": "cow", "monkey": "monkey", "bird": "bird", "snake": "snake",
}

OBJECTS: dict[str, str] = {
    "package": "package", "parcel": "package", "box": "package", "bag": "bag", "suitcase": "suitcase",
    "backpack": "backpack", "ladder": "ladder",
}

INFRASTRUCTURE: dict[str, str] = {"gate": "gate", "door": "door", "garage door": "garage door", "window": "window"}
OPEN_STATES = ("open", "opened", "ajar", "left open", "unlocked", "broken")

# items that describe a person rather than standing alone as a detection
CLOTHING: dict[str, str] = {
    "hi-vis vest": "hi-vis vest", "hoodie": "hoodie", "hooded": "hoodie", "jacket": "jacket", "coat": "coat",
    "raincoat": "raincoat", "vest": "vest", "uniform": "uniform", "cap": "cap", "hat": "hat",
    "helmet": "helmet", "t-shirt": "t-shirt", "shirt": "shirt", "overalls": "overalls", "sweater": "sweater",
}
CARRIED: dict[str, str] = {
    "backpack": "backpack", "bag": "bag", "crowbar": "crowbar", "bolt cutters": "bolt cutters",
    "flashlight": "flashlight", "torch": "flashlight", "phone": "phone", "package": "package",
    "parcel": "package", "box": "package", "ladder": "ladder", "tools": "tools", "clipboard": "clipboard",
}

# phrase -> canonical action (ordered: earlier phrases win when several match)
ACTIONS: list[tuple[str, str]] = [
    ("climbing", "climbing"), ("climbs", "climbing"), ("scaling", "climbing"), ("jumping over", "climbing"),
    ("tampering", "tampering"), ("prying", "tampering"), ("forcing", "tampering"), ("cutting", "tampering"),
    ("breaking", "tampering"), ("door handles", "tampering"), ("trying the handle", "tampering"),
    ("trying the door", "tampering"),
    ("peering", "peering"), ("looking into", "peering"), ("photographing", "peering"),
    ("loitering", "loitering"), ("lingering", "loitering"), ("pacing", "loitering"),
    ("hanging around", "loitering"), ("standing", "standing"), ("waiting", "standing"),
    ("entering", "entering"), ("enters", "entering"), ("entered", "entering"), ("arriving", "entering"),
    ("arrives", "entering"), ("pulls in", "entering"), ("pulling in", "entering"), ("driving in", "entering"),
    ("leaving", "exiting"), ("leaves", "exiting"), ("exiting", "exiting"), ("exits", "exiting"), ("exit", "exiting"),
    ("departing", "exiting"), ("driving out", "exiting"), ("pulling out", "exiting"),
    ("parked", "parked"), ("parking", "parked"), ("idling", "parked"),
    ("delivering", "delivering"), ("drops off", "delivering"), ("dropping off", "delivering"),
    ("unloading", "unloading"), ("loading", "loading"),
    ("running", "running"), ("runs", "running"), ("fleeing", "running"), ("walking", "walking"), ("walks", "walking"),
    ("walk", "walking"),
    ("crossing", "walking"), ("patrolling", "patrolling"), ("grazing", "grazing"), ("sleeping", "resting"),
]

SUSPICIOUS_ACTIONS = {"climbing", "tampering", "peering"}
SUSPICIOUS_ATTRIBUTES = {"masked", "balaclava", "crowbar", "bolt cutters", "face covered", "gloves at night"}
MASK_WORDS = ("masked", "balaclava", "face covered", "ski mask", "wearing a mask")

ZONE_ALIASES: list[tuple[str, str]] = [
    ("main gate", "main_gate"), ("front gate", "main_gate"), ("gate", "main_gate"), ("entrance gate", "main_gate"),
    ("driveway", "driveway"), ("parking", "driveway"),
    ("garage", "garage"),
    ("front door", "front_door"), ("porch", "front_door"), ("entrance", "front_door"),
    ("backyard", "backyard"), ("back yard", "backyard"), ("lawn", "backyard"), ("garden", "backyard"),
    ("back fence", "back_fence"), ("rear fence", "back_fence"), ("perimeter fence", "back_fence"),
    ("fence", "back_fence"),
    ("warehouse", "warehouse"), ("loading bay", "warehouse"), ("storage shed", "warehouse"),
    ("dock", "dock"), ("helipad", "dock"),
]

NEGATIONS = ("no", "without", "zero", "nobody", "none")
# "person no longer visible": negation that follows the noun
POST_NEGATIONS = ("no longer visible", "not visible", "no longer in view", "out of sight", "nowhere to be seen")
