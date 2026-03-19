# Script to create Item Groups and default Items for Equipment Report "Repair" parts,
# so the Report Equipment modal shows type-specific part lists (e.g. tractor: engine, gearbox, clutch).
#
# Run from bench root:
#   bench --site <your-site> execute f2c.scripts.populate_equipment_parts.run
# Dry run:
#   bench --site <your-site> execute f2c.scripts.populate_equipment_parts.run --kwargs '{"dry_run": true}'

from __future__ import annotations

import frappe


def _get_valid_gst_hsn_code_for_item(item_group: str | None) -> str | None:
    """
    Return a valid GST HSN Code name for Item creation when India Compliance is installed.
    Uses Item Group default if set, else a fallback code that exists in GST HSN Code.
    """
    if not frappe.db.table_exists("GST HSN Code"):
        return None
    try:
        from india_compliance.gst_india.utils import get_hsn_settings
        validate_hsn_code, valid_hsn_length = get_hsn_settings()
        if not validate_hsn_code:
            return None
    except Exception:
        return None

    def _valid(name):
        if not name or not frappe.db.exists("GST HSN Code", name):
            return False
        return len(str(name).strip()) in valid_hsn_length

    if item_group and frappe.get_meta("Item Group").has_field("default_gst_hsn_code"):
        group_code = frappe.db.get_value("Item Group", item_group, "default_gst_hsn_code")
        if _valid(group_code):
            return group_code
    for code in ("999900", "61149090", "843290", "843390", "998314", "8432", "8433", "9983", "9999"):
        if _valid(code):
            return code
    for name in frappe.get_all("GST HSN Code", pluck="name", order_by="name", limit=100):
        if len(str(name).strip()) in valid_hsn_length:
            return name
    return None

# Item groups used by EquipmentReportModal per category (Equipment Parts - Machinery, etc.)
EQUIPMENT_PARTS_GROUPS = [
    "Equipment Parts",  # Fallback for backward compatibility
    "Equipment Parts - Machinery",
    "Equipment Parts - Implement",
    "Equipment Parts - Hand Tool",
    "Equipment Parts - Other Tool",
    "Equipment Parts - Machinery - Tractor",
    "Equipment Parts - Machinery - Pump",
]

# Default items per group: (item_code, item_name). Item code must be unique across all groups.
# Machinery: generic (fallback for Sprayer, Generator, etc.)
DEFAULT_PARTS_MACHINERY = [
    ("EP-M-Engine", "Engine"),
    ("EP-M-Gearbox", "Gearbox"),
    ("EP-M-Clutch", "Clutch"),
    ("EP-M-Brake", "Brake"),
    ("EP-M-Steering", "Steering"),
    ("EP-M-Tyre", "Tyre"),
    ("EP-M-Hydraulics", "Hydraulics"),
    ("EP-M-Electrical", "Electrical"),
    ("EP-M-Fuel-System", "Fuel System"),
    ("EP-M-Cooling", "Cooling System"),
]
# Machinery - Tractor: tractor-specific parts (distinct codes to avoid clash with EP-M-*)
DEFAULT_PARTS_MACHINERY_TRACTOR = [
    ("EP-M-T-Engine", "Engine"),
    ("EP-M-T-Gearbox", "Gearbox"),
    ("EP-M-T-Clutch", "Clutch"),
    ("EP-M-T-Brake", "Brake"),
    ("EP-M-T-Steering", "Steering"),
    ("EP-M-T-Tyre", "Tyre"),
    ("EP-M-T-Hydraulics", "Hydraulics"),
    ("EP-M-T-Electrical", "Electrical"),
    ("EP-M-T-Fuel-System", "Fuel System"),
    ("EP-M-T-Cooling", "Cooling System"),
]
# Machinery - Pump: pump-specific parts
DEFAULT_PARTS_MACHINERY_PUMP = [
    ("EP-M-P-Impeller", "Impeller"),
    ("EP-M-P-Motor", "Motor"),
    ("EP-M-P-Seal", "Seal"),
    ("EP-M-P-Casing", "Casing"),
    ("EP-M-P-Shaft", "Shaft"),
    ("EP-M-P-Bearing", "Bearing"),
    ("EP-M-P-Gasket", "Gasket"),
    ("EP-M-P-Inlet-Outlet", "Inlet/Outlet"),
]
# Implement parts
DEFAULT_PARTS_IMPLEMENT = [
    ("EP-I-Blade", "Blade"),
    ("EP-I-Hitch", "Hitch"),
    ("EP-I-Wheel", "Wheel"),
    ("EP-I-Cutter", "Cutter"),
    ("EP-I-Frame", "Frame"),
    ("EP-I-Hydraulics", "Hydraulics"),
    ("EP-I-PTO", "PTO Shaft"),
    ("EP-I-Seeding-Unit", "Seeding Unit"),
    ("EP-I-Hopper", "Hopper"),
]
# Hand tool parts
DEFAULT_PARTS_HAND_TOOL = [
    ("EP-H-Handle", "Handle"),
    ("EP-H-Blade", "Blade"),
    ("EP-H-Grip", "Grip"),
    ("EP-H-Bolt", "Bolt / Fastener"),
    ("EP-H-Spring", "Spring"),
]
# Other tool parts
DEFAULT_PARTS_OTHER_TOOL = [
    ("EP-O-Motor", "Motor"),
    ("EP-O-Cable", "Cable"),
    ("EP-O-Battery", "Battery"),
    ("EP-O-Switch", "Switch"),
    ("EP-O-Housing", "Housing"),
]

GROUP_DEFAULT_ITEMS: dict[str, list[tuple[str, str]]] = {
    "Equipment Parts - Machinery": DEFAULT_PARTS_MACHINERY,
    "Equipment Parts - Implement": DEFAULT_PARTS_IMPLEMENT,
    "Equipment Parts - Hand Tool": DEFAULT_PARTS_HAND_TOOL,
    "Equipment Parts - Other Tool": DEFAULT_PARTS_OTHER_TOOL,
    "Equipment Parts - Machinery - Tractor": DEFAULT_PARTS_MACHINERY_TRACTOR,
    "Equipment Parts - Machinery - Pump": DEFAULT_PARTS_MACHINERY_PUMP,
}


def run(dry_run: bool = False) -> dict:
    """
    Create Equipment Parts Item Groups and default part items so the Report Equipment
    modal shows parts per equipment type (Machinery, Implement, Hand Tool, Other Tool).

    :param dry_run: If True, only print what would be done; do not commit.
    :return: Dict with item_groups_created, items_created, items_per_group.
    """
    stats: dict = {
        "item_groups_created": 0,
        "items_created": 0,
        "items_per_group": {},
    }

    # Ensure UOM Unit exists (used for part items)
    if not dry_run and not frappe.db.exists("UOM", "Unit"):
        frappe.get_doc({"doctype": "UOM", "uom_name": "Unit"}).insert(ignore_permissions=True)
        frappe.db.commit()

    for group_name in EQUIPMENT_PARTS_GROUPS:
        if not frappe.db.exists("Item Group", group_name):
            if dry_run:
                print(f"[DRY RUN] Would create Item Group: {group_name}")
            else:
                frappe.get_doc(
                    {"doctype": "Item Group", "item_group_name": group_name}
                ).insert(ignore_permissions=True)
                stats["item_groups_created"] = (stats["item_groups_created"] or 0) + 1
                print(f"Created Item Group: {group_name}")
        else:
            print(f"Item Group already exists: {group_name}")

        default_items = GROUP_DEFAULT_ITEMS.get(group_name, [])
        created_in_group = 0
        for item_code, item_name in default_items:
            if frappe.db.exists("Item", item_code):
                continue
            if dry_run:
                print(f"[DRY RUN] Would create Item: {item_code} ({item_name}) in {group_name}")
            else:
                item_payload = {
                    "doctype": "Item",
                    "item_code": item_code,
                    "item_name": item_name,
                    "item_group": group_name,
                    "stock_uom": "Unit",
                    "is_stock_item": 0,
                }
                gst_hsn_code = _get_valid_gst_hsn_code_for_item(group_name)
                if gst_hsn_code:
                    item_payload["gst_hsn_code"] = gst_hsn_code
                frappe.get_doc(item_payload).insert(ignore_permissions=True)
                created_in_group += 1
                print(f"Created Item: {item_code} ({item_name}) in {group_name}")
        if not dry_run and created_in_group:
            frappe.db.commit()
        stats["items_created"] = stats["items_created"] + created_in_group
        stats["items_per_group"][group_name] = created_in_group

    print(
        f"Done. Item groups created: {stats.get('item_groups_created', 0)}, "
        f"items created: {stats.get('items_created', 0)}"
    )
    return stats
