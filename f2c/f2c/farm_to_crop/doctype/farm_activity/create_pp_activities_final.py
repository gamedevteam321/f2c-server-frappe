import frappe

def create_pp_activities():
    """
    Create Plant Protection activities with same names as Nutrition Management
    """
    
    print("=" * 80)
    print("CREATING PLANT PROTECTION ACTIVITIES")
    print("=" * 80)
    
    application_methods = [
        "Basal Dose",
        "Pit Dose",
        "Top Dressing",
        "BroadCasting",
        "Spraying",
        "Drip - Fertigation",
        "Drenching"
    ]
    
    created = []
    errors = []
    
    for activity_name in application_methods:
        try:
            doc = frappe.get_doc({
                "doctype": "Farm Activity",
                "activity_name": activity_name,
                "activity_group_type": "Plant Protection",
                "parent_activity": None
            })
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
            
            created.append(activity_name)
            print(f"✓ Created: {activity_name} under Plant Protection")
            
        except Exception as e:
            errors.append(f"{activity_name}: {str(e)}")
            print(f"✗ Error: {activity_name} - {str(e)}")
            frappe.db.rollback()
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Created: {len(created)}")
    print(f"Errors: {len(errors)}")
    
    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  - {err}")

if __name__ == "__main__":
    create_pp_activities()

