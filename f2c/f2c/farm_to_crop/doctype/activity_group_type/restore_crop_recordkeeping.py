import frappe

def restore_crop_recordkeeping():
    """
    Restore Crop RecordKeeping entry
    """
    
    print("Checking Crop RecordKeeping...")
    
    if frappe.db.exists('Activity Group Type', 'Crop RecordKeeping'):
        print('✓ Crop RecordKeeping already exists')
        doc = frappe.get_doc('Activity Group Type', 'Crop RecordKeeping')
        print(f'  Code: {doc.code}')
    else:
        print('Creating Crop RecordKeeping...')
        doc = frappe.new_doc('Activity Group Type')
        doc.activity_group_type_name = 'Crop RecordKeeping'
        doc.code = 'RK'
        doc.insert()
        frappe.db.commit()
        print('✓ Created Crop RecordKeeping with code RK')

if __name__ == "__main__":
    restore_crop_recordkeeping()

