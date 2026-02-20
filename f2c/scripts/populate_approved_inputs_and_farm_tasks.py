# Script to:
# 1. Ensure Item Group "Approved Inputs" exists and populate Items in that category.
# 2. Create/update Farm Tasks (category "Approved Tank Mix") and populate their items table with these Items.
#
# Run from bench root:
#   bench --site <your-site> execute f2c.scripts.populate_approved_inputs_and_farm_tasks.run
# With options (dry_run, create_items_only, task_name):
#   bench --site <your-site> execute f2c.scripts.populate_approved_inputs_and_farm_tasks.run --kwargs '{"dry_run": true}'
#   bench --site <your-site> execute f2c.scripts.populate_approved_inputs_and_farm_tasks.run --kwargs '{"task_name": "My Approved Input Mix"}'

from __future__ import annotations

import frappe
from frappe.utils import flt

APPROVED_INPUTS_ITEM_GROUP = "Approved Inputs"
# Must match Farm Tasks doctype category options (default: "Approved Tank Mix")
FARM_TASK_CATEGORY_APPROVED_INPUT = "Approved Tank Mix"

# UOMs required for default items and Farm Task Item unit field
REQUIRED_UOMS = ["L", "ml", "kg", "g", "ml/L", "g/L", "Unit", "Nos"]

# Default items to create under Approved Inputs if they don't exist (item_code / item_name, default UOM)
DEFAULT_APPROVED_INPUT_ITEMS = [
    {"item_code": "Fertilizer-NPK", "item_name": "NPK Fertilizer", "stock_uom": "kg"},
    {"item_code": "Fertilizer-Urea", "item_name": "Urea", "stock_uom": "kg"},
    {"item_code": "Pesticide-Insecticide", "item_name": "Insecticide", "stock_uom": "L"},
    {"item_code": "Pesticide-Fungicide", "item_name": "Fungicide", "stock_uom": "L"},
    {"item_code": "Herbicide", "item_name": "Herbicide", "stock_uom": "L"},
    {"item_code": "Micro-Nutrients", "item_name": "Micro Nutrients", "stock_uom": "kg"},
    {"item_code": "Seed-Treatment", "item_name": "Seed Treatment", "stock_uom": "ml"},
    {"item_code": "Adjuvant", "item_name": "Adjuvant", "stock_uom": "ml/L"},
]

# Map Item stock_uom to Farm Task Item unit (allowed: kg, g, ml, L, ml/L, g/L, Bags/Acre, Per Manufacturer)
UOM_TO_FARM_TASK_UNIT = {
    "kg": "kg",
    "g": "g",
    "ml": "ml",
    "l": "L",
    "litre": "L",
    "litres": "L",
    "ml/l": "ml/L",
    "g/l": "g/L",
    "unit": "Per Manufacturer",
    "nos": "Per Manufacturer",
    "no": "Per Manufacturer",
    "bag": "Bags/Acre",
    "bags": "Bags/Acre",
}
DEFAULT_FARM_TASK_UNIT = "Per Manufacturer"


def run(
    dry_run: bool = False,
    create_items_only: bool = False,
    task_name: str | None = None,
    create_default_items: bool = True,
    populate_all_approved_input_tasks: bool = False,
) -> dict:
    """
    Ensure Approved Inputs item group and items exist, then populate Farm Tasks with those items.

    :param dry_run: If True, only print what would be done; do not commit.
    :param create_items_only: If True, only create item group and items; do not touch Farm Tasks.
    :param task_name: Name for the Farm Task to create/update. Default: "Approved Inputs - All". Ignored if populate_all_approved_input_tasks is True.
    :param create_default_items: If True, create DEFAULT_APPROVED_INPUT_ITEMS when no items in group exist.
    :param populate_all_approved_input_tasks: If True, add approved input items to all existing Farm Tasks with category "Approved Tank Mix" (and ensure one default task exists).
    :return: Dict with counts (items_created, items_in_group, tasks_created, tasks_updated, task_items_added).
    """
    stats = {
        "item_group_created": False,
        "items_created": 0,
        "items_in_group": 0,
        "tasks_created": 0,
        "tasks_updated": 0,
        "task_items_added": 0,
    }

    # 0) Ensure required UOMs exist (for Items and Farm Task Item unit field)
    if not dry_run:
        uoms_created = 0
        for uom_name in REQUIRED_UOMS:
            if not frappe.db.exists("UOM", uom_name):
                frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(
                    ignore_permissions=True
                )
                print(f"Created UOM: {uom_name}")
                uoms_created += 1
        if uoms_created:
            frappe.db.commit()

    # 1) Item Group "Approved Inputs"
    if not frappe.db.exists("Item Group", APPROVED_INPUTS_ITEM_GROUP):
        if dry_run:
            print(f"[DRY RUN] Would create Item Group: {APPROVED_INPUTS_ITEM_GROUP}")
        else:
            ig = frappe.get_doc(
                {"doctype": "Item Group", "item_group_name": APPROVED_INPUTS_ITEM_GROUP}
            )
            ig.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"Created Item Group: {APPROVED_INPUTS_ITEM_GROUP}")
        stats["item_group_created"] = True
    else:
        print(f"Item Group already exists: {APPROVED_INPUTS_ITEM_GROUP}")

    # 2) Create default items in Approved Inputs if none exist
    existing_items = frappe.get_all(
        "Item",
        filters={"item_group": APPROVED_INPUTS_ITEM_GROUP},
        fields=["name", "item_code", "item_name", "stock_uom"],
    )
    stats["items_in_group"] = len(existing_items)

    if create_default_items and len(existing_items) == 0:
        for def_item in DEFAULT_APPROVED_INPUT_ITEMS:
            item_code = def_item.get("item_code") or def_item.get("item_name")
            if frappe.db.exists("Item", item_code):
                if not dry_run:
                    # Link existing item to Approved Inputs
                    doc = frappe.get_doc("Item", item_code)
                    if doc.item_group != APPROVED_INPUTS_ITEM_GROUP:
                        doc.item_group = APPROVED_INPUTS_ITEM_GROUP
                        doc.save(ignore_permissions=True)
                        stats["items_created"] += 1
                continue
            if dry_run:
                print(f"[DRY RUN] Would create Item: {item_code} ({def_item.get('item_name')})")
            else:
                item_doc = frappe.get_doc(
                    {
                        "doctype": "Item",
                        "item_code": item_code,
                        "item_name": def_item.get("item_name", item_code),
                        "item_group": APPROVED_INPUTS_ITEM_GROUP,
                        "stock_uom": def_item.get("stock_uom", "Unit"),
                        "is_stock_item": 1,
                    }
                )
                item_doc.insert(ignore_permissions=True)
                stats["items_created"] += 1
                print(f"Created Item: {item_code}")
        if not dry_run and stats["items_created"]:
            frappe.db.commit()
        existing_items = frappe.get_all(
            "Item",
            filters={"item_group": APPROVED_INPUTS_ITEM_GROUP},
            fields=["name", "item_code", "item_name", "stock_uom"],
        )
        stats["items_in_group"] = len(existing_items)

    if create_items_only:
        print(f"Done (items only). Items in '{APPROVED_INPUTS_ITEM_GROUP}': {stats['items_in_group']}")
        return stats

    if not existing_items:
        print("No items in Approved Inputs. Create items first or use create_default_items=True.")
        return stats

    def add_items_to_task(doc, items_list, task_label: str) -> int:
        existing_codes = {row.item for row in (doc.items or [])}
        added = 0
        for item_row in items_list:
            item_code = item_row.get("name") or item_row.get("item_code")
            if not item_code or item_code in existing_codes:
                continue
            unit = _item_uom_to_farm_task_unit(item_row.get("stock_uom") or "Unit")
            doc.append(
                "items",
                {
                    "item": item_code,
                    "item_name": item_row.get("item_name") or item_code,
                    "quantity": flt(1.0, 3),
                    "unit": unit,
                },
            )
            existing_codes.add(item_code)
            added += 1
        if added:
            doc.save(ignore_permissions=True)
            print(f"Added {added} item(s) to Farm Task '{task_label}'.")
        return added

    if populate_all_approved_input_tasks:
        # Ensure default task exists, then add items to it and to any other "Approved Input" tasks
        default_name = task_name or "Approved Inputs - All"
        if not frappe.db.exists("Farm Tasks", default_name):
            if dry_run:
                print(f"[DRY RUN] Would create Farm Task: {default_name}")
            else:
                task_doc = frappe.get_doc(
                    {
                        "doctype": "Farm Tasks",
                        "task_name": default_name,
                        "category": FARM_TASK_CATEGORY_APPROVED_INPUT,
                        "description": "All approved input items from Approved Inputs item group.",
                    }
                )
                task_doc.insert(ignore_permissions=True)
                stats["tasks_created"] = 1
                stats["task_items_added"] += add_items_to_task(task_doc, existing_items, default_name)
        else:
            if not dry_run:
                task_doc = frappe.get_doc("Farm Tasks", default_name)
                stats["task_items_added"] += add_items_to_task(task_doc, existing_items, default_name)
            stats["tasks_updated"] = 1

        all_tasks = frappe.get_all(
            "Farm Tasks",
            filters={"category": FARM_TASK_CATEGORY_APPROVED_INPUT},
            fields=["name", "task_name"],
        )
        for t in all_tasks:
            if t["name"] == (task_name or "Approved Inputs - All"):
                continue
            if dry_run:
                print(f"[DRY RUN] Would add approved input items to Farm Task: {t['name']}")
            else:
                task_doc = frappe.get_doc("Farm Tasks", t["name"])
                added = add_items_to_task(task_doc, existing_items, t["name"])
                stats["task_items_added"] += added
                if added:
                    stats["tasks_updated"] += 1
        if not dry_run and stats["task_items_added"]:
            frappe.db.commit()
    else:
        # Single Farm Task to create or update
        target_task_name = task_name or "Approved Inputs - All"
        task_doc = None
        if frappe.db.exists("Farm Tasks", target_task_name):
            if dry_run:
                print(f"[DRY RUN] Would update Farm Task: {target_task_name}")
            else:
                task_doc = frappe.get_doc("Farm Tasks", target_task_name)
            stats["tasks_updated"] = 1
        else:
            if dry_run:
                print(f"[DRY RUN] Would create Farm Task: {target_task_name} (category: {FARM_TASK_CATEGORY_APPROVED_INPUT})")
            else:
                task_doc = frappe.get_doc(
                    {
                        "doctype": "Farm Tasks",
                        "task_name": target_task_name,
                        "category": FARM_TASK_CATEGORY_APPROVED_INPUT,
                        "description": "All approved input items from Approved Inputs item group.",
                    }
                )
                task_doc.insert(ignore_permissions=True)
                stats["tasks_created"] = 1
                print(f"Created Farm Task: {target_task_name}")

        if not dry_run and task_doc:
            added = add_items_to_task(task_doc, existing_items, target_task_name)
            stats["task_items_added"] = added
            if added:
                frappe.db.commit()

    print(
        f"Summary: item_group_created={stats['item_group_created']}, items_created={stats['items_created']}, "
        f"items_in_group={stats['items_in_group']}, tasks_created={stats['tasks_created']}, "
        f"tasks_updated={stats['tasks_updated']}, task_items_added={stats['task_items_added']}"
    )
    return stats


def _item_uom_to_farm_task_unit(stock_uom: str) -> str:
    """Map Item stock_uom to Farm Task Item unit."""
    if not stock_uom:
        return DEFAULT_FARM_TASK_UNIT
    key = (stock_uom or "").strip().lower()
    return UOM_TO_FARM_TASK_UNIT.get(key, DEFAULT_FARM_TASK_UNIT)
