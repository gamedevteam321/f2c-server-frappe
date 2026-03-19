import frappe

def final_report():
    """
    Generate final report of all Activity Group Types
    """
    
    print("=" * 80)
    print("ACTIVITY GROUP TYPES - FINAL REPORT")
    print("=" * 80)
    
    # Expected entries from the image
    expected = [
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
    
    # Get all Activity Group Types
    activity_groups = frappe.get_all(
        "Activity Group Type",
        fields=["activity_group_type_name", "code"],
        order_by="code asc, activity_group_type_name asc"
    )
    
    print(f"\nTotal Activity Groups: {len(activity_groups)}")
    print(f"Expected from image: {len(expected)}\n")
    
    print(f"{'Code':<6} {'Activity Group Name':<45}")
    print("-" * 80)
    
    for group in activity_groups:
        print(f"{group['code']:<6} {group['activity_group_type_name']:<45}")
    
    # Check for missing entries
    print("\n" + "=" * 80)
    print("VALIDATION")
    print("=" * 80)
    
    existing_names = {g['activity_group_type_name'] for g in activity_groups}
    missing = []
    
    for exp in expected:
        if exp['name'] not in existing_names:
            missing.append(exp['name'])
    
    if missing:
        print(f"\n⚠️  Missing {len(missing)} expected entries:")
        for name in missing:
            print(f"  • {name}")
    else:
        print("\n✓ All expected entries are present!")
    
    # Check for duplicate codes
    code_count = {}
    for group in activity_groups:
        code = group['code']
        if code not in code_count:
            code_count[code] = []
        code_count[code].append(group['activity_group_type_name'])
    
    duplicates = {code: names for code, names in code_count.items() if len(names) > 1}
    
    if duplicates:
        print(f"\n⚠️  Found {len(duplicates)} duplicate code(s):")
        for code, names in duplicates.items():
            print(f"\n  Code '{code}' is used by:")
            for name in names:
                print(f"    • {name}")
        print("\n  Note: This matches the source image where both Livestock Management")
        print("        and Assurance Management have code 'AM'.")
    else:
        print("\n✓ No duplicate codes found!")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    final_report()

