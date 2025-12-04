import frappe

def verify_activity_groups():
    """
    Verify all Activity Group Type entries
    """
    
    print("=" * 80)
    print("ACTIVITY GROUP TYPES - VERIFICATION")
    print("=" * 80)
    
    # Get all Activity Group Types ordered by name
    activity_groups = frappe.get_all(
        "Activity Group Type",
        fields=["activity_group_type_name", "code"],
        order_by="activity_group_type_name asc"
    )
    
    print(f"\nTotal Activity Groups: {len(activity_groups)}\n")
    print(f"{'Activity Group Name':<40} {'Code':<10}")
    print("-" * 80)
    
    for group in activity_groups:
        print(f"{group['activity_group_type_name']:<40} {group['code']:<10}")
    
    print("\n" + "=" * 80)

if __name__ == "__main__":
    verify_activity_groups()

