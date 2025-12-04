import frappe
import os
import shutil

def rename_doctype():
    """
    Rename POP-Activity List to Farm Crop Activity Mapping
    """
    
    old_name = "POP-Activity List"
    new_name = "Farm Crop Activity Mapping"
    old_module_name = "pop_activity_list"
    new_module_name = "farm_crop_activity_mapping"
    
    print("=" * 80)
    print("RENAMING DOCTYPE")
    print("=" * 80)
    print(f"Old Name: {old_name}")
    print(f"New Name: {new_name}")
    print("=" * 80)
    
    try:
        # Use Frappe's built-in rename_doc function
        frappe.rename_doc("DocType", old_name, new_name, force=True)
        frappe.db.commit()
        
        print(f"\n✓ Successfully renamed doctype from '{old_name}' to '{new_name}'")
        print("\nThis will automatically update:")
        print("  - Database table name")
        print("  - All Link field references")
        print("  - All foreign key constraints")
        
        print("\n" + "=" * 80)
        print("IMPORTANT: MANUAL STEPS REQUIRED")
        print("=" * 80)
        print("\nAfter this script completes, you need to:")
        print(f"\n1. Rename the folder:")
        print(f"   FROM: apps/f2c/f2c/farm_to_crop/doctype/{old_module_name}/")
        print(f"   TO:   apps/f2c/f2c/farm_to_crop/doctype/{new_module_name}/")
        
        print(f"\n2. Rename the files inside the folder:")
        print(f"   - {old_module_name}.py → {new_module_name}.py")
        print(f"   - {old_module_name}.js → {new_module_name}.js")
        print(f"   - {old_module_name}.json → {new_module_name}.json")
        
        print(f"\n3. Update class name in {new_module_name}.py:")
        print(f"   FROM: class POPActivityList(Document):")
        print(f"   TO:   class FarmCropActivityMapping(Document):")
        
        print(f"\n4. Update any custom queries or code references")
        
        print(f"\n5. Run: bench migrate")
        print(f"\n6. Run: bench clear-cache")
        print(f"\n7. Run: bench build")
        
        print("\n" + "=" * 80)
        
    except Exception as e:
        print(f"\n✗ Error: {str(e)}")
        frappe.db.rollback()
        return False
    
    return True

if __name__ == "__main__":
    rename_doctype()

