"""Caption -> detection parsing (the default, offline frame analyzer)."""
import pytest

from drone_security.perception import RuleBasedAnalyzer

parse = RuleBasedAnalyzer().parse


def test_truck_parsed_with_colour_make_model_and_zone():
    [truck] = parse("Frame 1: Blue Ford F150 parked at garage")
    assert (truck.category, truck.label, truck.color, truck.make, truck.model) == ("vehicle", "truck", "blue", "Ford", "F150")
    assert truck.zone == "garage" and truck.action == "parked"
    assert truck.display_name == "Blue Ford F150"


@pytest.mark.parametrize("variant", ["blue ford f-150", "Blue Ford F 150 pickup truck", "BLUE F150"])
def test_model_spelling_variants_normalise(variant):
    [truck] = parse(f"{variant} entering through main gate")
    assert truck.model == "F150" and truck.make == "Ford" and truck.action == "entering"


def test_person_with_clothing_and_loitering():
    [person] = parse("Person in dark hoodie loitering near main gate")
    assert person.category == "person" and person.action == "loitering" and person.zone == "main_gate"
    assert person.color == "dark" and "hoodie" in person.attributes
    assert person.display_name == "Person in dark hoodie"


def test_masked_intruder_with_tools():
    [person] = parse("Masked man in black jacket climbing the back fence carrying bolt cutters")
    assert person.action == "climbing" and person.zone == "back_fence"
    assert {"masked", "bolt cutters", "jacket"} <= set(person.attributes)


def test_multiple_objects_in_one_caption():
    dets = parse("Courier in hi-vis vest drops off a package at front door; white delivery van idling in driveway")
    assert [(d.category, d.label) for d in dets] == [("person", "person"), ("object", "package"), ("vehicle", "van")]
    assert dets[0].action == "delivering" and dets[2].zone == "driveway" and dets[2].color == "white"


@pytest.mark.parametrize("caption", [
    "Empty driveway, no vehicles or people visible",
    "Backyard clear, no movement",
    "Back fence intact, no one nearby",
    "Garage door closed, no activity",
])
def test_empty_scenes_and_negations_produce_nothing(caption):
    assert parse(caption) == []


def test_carried_object_attaches_to_person_and_keeps_action():
    [person] = parse("Person carrying a box walking to warehouse")
    assert "package" in person.attributes and person.action == "walking" and person.zone == "warehouse"


def test_open_access_points():
    [gate] = parse("Main gate left open, no one around")
    assert (gate.category, gate.label, gate.zone, gate.attributes) == ("infrastructure", "gate", "main_gate", ["open"])
    [door] = parse("Warehouse side door ajar")
    assert door.zone == "warehouse"


def test_counts_from_number_words_and_groups():
    [people] = parse("Two people walking across backyard")
    assert people.count == 2
    [workers] = parse("A group of workers unloading at warehouse")
    assert workers.count == 3


def test_clock_time_is_not_read_as_a_count():
    [car] = parse("At 23:10 a white Toyota Camry arrives in driveway")
    assert car.count == 1 and car.action == "entering"


def test_colour_does_not_bleed_between_objects():
    person, truck = parse("Person in dark hoodie standing next to a truck")
    assert person.color == "dark" and truck.color is None


def test_zone_falls_back_to_drone_position():
    [deer] = parse("Deer grazing", default_zone="backyard")
    assert deer.zone == "backyard" and deer.category == "animal"
