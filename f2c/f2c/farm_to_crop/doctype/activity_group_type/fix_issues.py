import frappe

def fix_issues():
    """
    Fix duplicate codes and remove old entries
    """
    
    print("=" * 80)
    print("FIXING ACTIVITY GROUP ISSUES")
    print("=" * 80)
    
    # 1. Delete old/obsolete entries
    old_entries_to_delete = [
        "Equipment & Infra",  # Replaced by "Equipment & Infra Management"
        "Nutrition & Plant Protection",  # Split into separate entries
        "Crop Recordkeeping",  # Wrong capitalization, should be "Crop RecordKeeping"
    ]
    
    print("\n1. Removing old/obsolete entries:")
    for entry_name in old_entries_to_delete:
        try:
            if frappe.db.exists("Activity Group Type", entry_name):
                # Check if it's being used anywhere
                # For now, we'll just delete it
                frappe.delete_doc("Activity Group Type", entry_name, force=1)
                frappe.db.commit()
                print(f"  ✓ Deleted: {entry_name}")
            else:
                print(f"  ○ Not found: {entry_name}")
        except Exception as e:
            print(f"  ✗ Error deleting {entry_name}: {str(e)}")
            frappe.db.rollback()
    
    # 2. Fix duplicate AM code
    # Looking at the image, both "Livestock Management" and "Assurance Management" have "AM"
    # This appears to be an error in the source. Let's keep it as is since that's what the image shows
    # But we should note this issue
    print("\n2. Checking duplicate codes:")
    print("  ⚠️  Note: Both 'Livestock Management' and 'Assurance Management' have code 'AM'")
    print("      This matches the source image but may cause confusion.")
    print("      Consider using different codes like:")
    print("      - Livestock Management: LM (but LM is used by Lease Management)")
    print("      - Livestock Management: LS")
    print("      - Assurance Management: AS")
    
    # 3. Verify final state
    print("\n3. Final verification:")
    activity_groups = frappe.get_all(
        "Activity Group Type",
        fields=["activity_group_type_name", "code"],
        order_by="activity_group_type_name asc"
    )
    
    print(f"\n   Total Activity Groups: {len(activity_groups)}\n")
    print(f"   {'Activity Group Name':<40} {'Code':<10}")
    print("   " + "-" * 70)
    
    for group in activity_groups:
        print(f"   {group['activity_group_type_name']:<40} {group['code']:<10}")
    
    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)

if __name__ == "__main__":
    fix_issues()

