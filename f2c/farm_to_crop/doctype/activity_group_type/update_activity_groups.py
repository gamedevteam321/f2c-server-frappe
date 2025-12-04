import frappe

def update_activity_groups():
    """
    Update or create Activity Group Type entries based on Operations - Activity Groups table
    """
    
    # Activity Group Name -> Code mapping from the image
    activity_groups = [
        {"name": "Field Planning", "code": "FP"},
        {"name": "Lease Management", "code": "LM"},
        {"name": "Land Preparation", "code": "LP"},
        {"name": "Plant Management", "code": "PM"},
        {"name": "Floor Management", "code": "FM"},
        {"name": "Nutrition Management", "code": "NM"},
        {"name": "Plant Protection", "code": "PP"},
        {"name": "Crop RecordKeeping", "code": "RK"},
        {"name": "Harvest Management", "code": "HM"},
        {"name": "On Field Storage & Processing", "code": "FS"},
        {"name": "Livestock Management", "code": "AM"},
        {"name": "Labour Management", "code": "LB"},
        {"name": "Equipment & Infra Management", "code": "EI"},
        {"name": "R&D/Trial Management", "code": "RT"},
        {"name": "Assurance Management", "code": "AM"},
        {"name": "Inputs Purchase", "code": "IP"},
        {"name": "Inventory & Logistics Management", "code": "IL"},
    ]
    
    created = []
    updated = []
    skipped = []
    errors = []
    
    print("=" * 80)
    print("UPDATING ACTIVITY GROUP TYPES")
    print("=" * 80)
    
    for group in activity_groups:
        try:
            name = group["name"]
            code = group["code"]
            
            # Check if activity group already exists
            if frappe.db.exists("Activity Group Type", name):
                # Update existing record
                doc = frappe.get_doc("Activity Group Type", name)
                
                # Check if code needs updating
                if doc.code != code:
                    old_code = doc.code
                    doc.code = code
                    doc.save()
                    frappe.db.commit()
                    updated.append(name)
                    print(f"✓ Updated: {name} (Code: {old_code} → {code})")
                else:
                    skipped.append(name)
                    print(f"○ Skipped: {name} (Code: {code}) - Already correct")
            else:
                # Create new record
                doc = frappe.new_doc("Activity Group Type")
                doc.activity_group_type_name = name
                doc.code = code
                doc.insert()
                frappe.db.commit()
                created.append(name)
                print(f"✓ Created: {name} (Code: {code})")
                
        except Exception as e:
            error_msg = f"{name}: {str(e)}"
            errors.append(error_msg)
            print(f"✗ Error: {error_msg}")
            frappe.db.rollback()
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Created:  {len(created)} activity groups")
    print(f"Updated:  {len(updated)} activity groups")
    print(f"Skipped:  {len(skipped)} activity groups (already correct)")
    print(f"Errors:   {len(errors)}")
    
    if created:
        print(f"\nCreated:")
        for item in created:
            print(f"  • {item}")
    
    if updated:
        print(f"\nUpdated:")
        for item in updated:
            print(f"  • {item}")
    
    if errors:
        print(f"\nErrors:")
        for error in errors:
            print(f"  • {error}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    update_activity_groups()

