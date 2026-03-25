import frappe

def fix_sa_sb_values():
    """
    Fix Stress Free A (SA) and Stress Free B (SB) values that weren't updated
    """
    
    # Tasks that have SA and SB
    tasks_with_sa_sb = ['4SSPCA', '4SSPZN', '3SSP']
    
    print("Checking and fixing SA and SB values...\n")
    
    for task_name in tasks_with_sa_sb:
        try:
            doc = frappe.get_doc('Farm Tasks', task_name)
            print(f"\n{task_name}:")
            print("  Before:")
            
            updated = False
            for item in doc.items:
                print(f"    {item.item}: {item.quantity} {item.unit}")
                
                # Update SA/Sa to 1.25 ml/L
                if item.item in ['SA', 'Sa'] and item.quantity != 1.25:
                    item.quantity = 1.25
                    item.unit = 'ml/L'
                    updated = True
                    print(f"      → Updating {item.item} to 1.25 ml/L")
                
                # Update SB/Sb to 1.25 ml/L
                if item.item in ['SB', 'Sb'] and item.quantity != 1.25:
                    item.quantity = 1.25
                    item.unit = 'ml/L'
                    updated = True
                    print(f"      → Updating {item.item} to 1.25 ml/L")
            
            if updated:
                doc.save()
                frappe.db.commit()
                print("  ✓ Updated and saved")
                
                print("  After:")
                for item in doc.items:
                    if item.item in ['SA', 'SB', 'Sa', 'Sb']:
                        print(f"    {item.item}: {item.quantity} {item.unit}")
            else:
                print("  ✓ Already correct")
                
        except Exception as e:
            print(f"  ✗ Error: {str(e)}")
            frappe.db.rollback()
    
    print("\n=== Done ===")

if __name__ == "__main__":
    fix_sa_sb_values()

