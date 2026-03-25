# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


# Water source types commonly used in India (agriculture, rural & urban supply)
WATER_SOURCES_INDIA = [
    {"water_source_name": "Canal", "description": "Irrigation canal from rivers or reservoirs. Common under major/medium irrigation projects."},
    {"water_source_name": "Tube Well", "description": "Borewell extracting groundwater; widely used for irrigation and drinking."},
    {"water_source_name": "Borewell", "description": "Deep bore for groundwater extraction; often used interchangeably with tube well."},
    {"water_source_name": "Open Well", "description": "Traditional dug well; shallow or medium depth groundwater."},
    {"water_source_name": "Dug Well", "description": "Hand-dug or excavated well for groundwater or surface seepage."},
    {"water_source_name": "Tank", "description": "Surface storage tank or pond for irrigation/drinking; includes village tanks and farm ponds."},
    {"water_source_name": "Pond", "description": "Natural or constructed water body for storage and irrigation."},
    {"water_source_name": "River", "description": "Direct abstraction or lift from river; seasonal in many regions."},
    {"water_source_name": "Lake", "description": "Natural or man-made lake used for irrigation or supply."},
    {"water_source_name": "Reservoir", "description": "Dam reservoir; source for canal systems and drinking water schemes."},
    {"water_source_name": "Stream", "description": "Small river or stream; often seasonal (monsoon)."},
    {"water_source_name": "Nala", "description": "Drainage channel or seasonal stream; sometimes used for recharge or lift."},
    {"water_source_name": "Check Dam", "description": "Small structure across stream/nalla to store water and recharge groundwater."},
    {"water_source_name": "Percolation Tank", "description": "Tank designed to recharge groundwater; common in Maharashtra and other states."},
    {"water_source_name": "Farm Pond", "description": "On-farm dug-out or lined pond for irrigation and rainwater harvesting."},
    {"water_source_name": "Submersible Pump", "description": "Pump in borewell/tube well for lifting groundwater."},
    {"water_source_name": "Lift Irrigation", "description": "Scheme lifting water from river/canal/reservoir to higher command area."},
    {"water_source_name": "Rainwater Harvesting", "description": "Roof or surface runoff collected and stored for use."},
    {"water_source_name": "Surface Water", "description": "Generic surface source: river, tank, canal, etc."},
    {"water_source_name": "Groundwater", "description": "Water from well or borewell (generic)."},
    {"water_source_name": "Municipal Supply", "description": "Piped water from urban/rural water supply scheme."},
    {"water_source_name": "Tanker", "description": "Water supplied by tanker truck; common in water-scarce areas."},
    {"water_source_name": "River Lift", "description": "Lift scheme drawing from river (e.g. for drinking or irrigation)."},
    {"water_source_name": "Canal Lift", "description": "Lift from canal to serve areas above canal level."},
    {"water_source_name": "Kere", "description": "Traditional tank system (e.g. Karnataka, Tamil Nadu) for irrigation and recharge."},
    {"water_source_name": "Other", "description": "Any other water source not listed above."},
]


def seed_water_source():
    """Seed Water Source with types commonly available in India. Idempotent."""
    for row in WATER_SOURCES_INDIA:
        name = row["water_source_name"]
        if frappe.db.exists("Water Source", name):
            continue
        doc = frappe.get_doc({
            "doctype": "Water Source",
            "water_source_name": name,
            "description": row.get("description") or "",
        })
        doc.insert(ignore_permissions=True)
    frappe.db.commit()


if __name__ == "__main__":
    frappe.connect()
    seed_water_source()
