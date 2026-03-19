import frappe

def restore_plant_protection_activities():
    """
    Recreate Plant Protection activities with the same names as Nutrition Management
    We'll need to modify the Farm Activity doctype to remove the unique constraint on activity_name
    OR use a different naming approach
    """
    
    print("=" * 80)
    print("RESTORING PLANT PROTECTION ACTIVITIES")
    print("=" * 80)
    
    # Check if Plant Protection group exists
    protection_group = "Plant Protection"
    if not frappe.db.exists("Activity Group Type", protection_group):
        print(f"\n✗ Error: Activity Group '{protection_group}' does not exist!")
        return
    
    # Application methods to add
    application_methods = [
        "Basal Dose",
        "Pit Dose",
        "Top Dressing",
        "BroadCasting",
        "Spraying",
        "Drip - Fertigation",
        "Drenching"
    ]
    
    print(f"\nAttempting to create {len(application_methods)} activities under '{protection_group}'...")
    print("\nNote: Since activity_name has unique=1 constraint, we need to check if this works.\n")
    
    created = []
    errors = []
    
    for activity_name in application_methods:
        try:
            # Check if activity with this name already exists
            existing = frappe.db.get_value(
                "Farm Activity",
                {"activity_name": activity_name},
                ["name", "activity_group_type"],
                as_dict=True
            )
            
            if existing:
                print(f"○ Activity '{activity_name}' already exists under '{existing.activity_group_type}' ({existing.name})")
                print(f"  Cannot create duplicate under Plant Protection due to unique constraint")
                errors.append(f"{activity_name} - already exists")
                continue
            
            # Try to create new activity
            doc = frappe.get_doc({
                "doctype": "Farm Activity",
                "activity_name": activity_name,
                "activity_group_type": protection_group,
                "parent_activity": None
            })
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            
            created.append(activity_name)
            print(f"✓ Created: {activity_name}")
            
        except Exception as e:
            error_msg = f"Error creating {activity_name}: {str(e)}"
            errors.append(error_msg)
            print(f"✗ {error_msg}")
            frappe.db.rollback()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Created: {len(created)} activities")
    print(f"Errors/Skipped: {len(errors)}")
    
    if errors:
        print(f"\nIssues encountered:")
        for err in errors:
            print(f"  - {err}")
    
    print("\n" + "=" * 80)
    print("SOLUTION NEEDED")
    print("=" * 80)
    print("\nThe Farm Activity doctype has 'unique=1' on activity_name field.")
    print("We have two options:")
    print("\n1. Remove unique constraint from activity_name field")
    print("   - Allows same activity names under different groups")
    print("   - Requires doctype modification")
    print("\n2. Keep current design (activities under Nutrition Management)")
    print("   - Use same activities for both purposes")
    print("   - Context determined by Farm Tasks/Items used")
    print("\nWhich approach would you prefer?")
    print("=" * 80)

if __name__ == "__main__":
    restore_plant_protection_activities()

