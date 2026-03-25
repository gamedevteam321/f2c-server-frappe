import frappe

def cleanup_duplicates():
    """
    Check for duplicate codes and old entries that need cleanup
    """
    
    print("=" * 80)
    print("CHECKING FOR ISSUES")
    print("=" * 80)
    
    # Get all Activity Group Types
    activity_groups = frappe.get_all(
        "Activity Group Type",
        fields=["name", "activity_group_type_name", "code"],
        order_by="activity_group_type_name asc"
    )
    
    # Check for duplicate codes
    code_map = {}
    duplicates = []
    
    for group in activity_groups:
        code = group['code']
        if code in code_map:
            duplicates.append({
                'code': code,
                'entries': [code_map[code], group]
            })
        else:
            code_map[code] = group
    
    if duplicates:
        print(f"\n⚠️  Found {len(duplicates)} duplicate code(s):\n")
        for dup in duplicates:
            print(f"Code '{dup['code']}' is used by:")
            for entry in dup['entries']:
                print(f"  • {entry['activity_group_type_name']}")
            print()
    
    # Check for old/obsolete entries
    # These are entries that might be old versions or typos
    potential_old_entries = [
        "Equipment & Infra",  # Should be "Equipment & Infra Management"
        "Nutrition & Plant Protection",  # Split into separate entries now
        "Crop Recordkeeping",  # Check if this is different from "Crop RecordKeeping"
    ]
    
    old_entries_found = []
    for group in activity_groups:
        if group['activity_group_type_name'] in potential_old_entries:
            old_entries_found.append(group)
    
    if old_entries_found:
        print(f"\n⚠️  Found {len(old_entries_found)} potential old/obsolete entry(ies):\n")
        for entry in old_entries_found:
            print(f"  • {entry['activity_group_type_name']} (Code: {entry['code']})")
        print()
    
    # Show all current entries grouped by code
    print("\n" + "=" * 80)
    print("ENTRIES GROUPED BY CODE")
    print("=" * 80)
    
    codes = {}
    for group in activity_groups:
        code = group['code']
        if code not in codes:
            codes[code] = []
        codes[code].append(group['activity_group_type_name'])
    
    for code in sorted(codes.keys()):
        print(f"\n{code}:")
        for name in codes[code]:
            print(f"  • {name}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    cleanup_duplicates()

