import frappe

def verify_all_tasks():
    """
    Verify all Farm Tasks have correct dosage values
    """
    
    all_tasks = [
        "1MH", "1SP", "2VN", "2WG", "3OBCBMFM", "2OBCBM", "1HP",
        "6ABNKPG", "5ABKPG", "5ANKPG", "4ANKG", "3AKG", "2BK", "2BWS",
        "1WS", "1OBC", "1SS", "4SSPCA", "4SSPZN", "2PK", "3SSP",
        "2ODP", "3ODPMA", "4ODPAZNC", "3ODPNC", "4ODPVMIV", "3ODPVM"
    ]
    
    print("=" * 80)
    print("FARM TASKS DOSAGE VERIFICATION")
    print("=" * 80)
    
    for task_name in all_tasks:
        try:
            doc = frappe.get_doc('Farm Tasks', task_name)
            print(f"\n{task_name}:")
            
            for item in doc.items:
                print(f"  • {item.item_name or item.item}: {item.quantity} {item.unit}")
                
        except Exception as e:
            print(f"\n{task_name}: ERROR - {str(e)}")
    
    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    verify_all_tasks()

