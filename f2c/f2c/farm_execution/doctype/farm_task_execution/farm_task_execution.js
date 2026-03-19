frappe.ui.form.on('Farm Task Execution', {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.is_spray) {
			frm.add_custom_button(__('Recalculate Inputs (Planned Water)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.recalculate_inputs',
					args: {
						execution_name: frm.doc.name,
						use_actual_water: 0
					},
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Actions'));

			frm.add_custom_button(__('Recalculate Inputs (Actual Water)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.recalculate_inputs',
					args: {
						execution_name: frm.doc.name,
						use_actual_water: 1
					},
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Actions'));
		}

		if (!frm.is_new()) {
			frm.add_custom_button(__('Issue Inputs (Stock Entry)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.issue_inputs',
					args: { execution_name: frm.doc.name },
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Stock'));

			frm.add_custom_button(__('Return Inputs (Stock Entry)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.return_inputs',
					args: { execution_name: frm.doc.name },
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Stock'));
		}

		if (!frm.is_new()) {
			frm.add_custom_button(__('Labour Check-in (IN)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.mark_checkin_in',
					args: { execution_name: frm.doc.name },
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Labour'));

			frm.add_custom_button(__('Labour Check-in (OUT)'), function() {
				frappe.call({
					method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.mark_checkin_out',
					args: { execution_name: frm.doc.name },
					callback(r) {
						if (r.message) frm.reload_doc();
					}
				});
			}, __('Labour'));
		}

		if (!frm.is_new() && frm.doc.schedule_ref) return;

		// Assist creation from schedule
		if (frm.is_new()) {
			frm.add_custom_button(__('Create from Schedule'), function() {
				frappe.prompt(
					[
						{
							fieldname: 'schedule_ref',
							fieldtype: 'Link',
							label: __('Schedule'),
							options: 'Crop Plan Schedule',
							reqd: 1
						}
					],
					function(values) {
						frappe.call({
							method: 'f2c.farm_execution.doctype.farm_task_execution.farm_task_execution.create_from_schedule',
							args: { schedule_name: values.schedule_ref },
							callback(r) {
								if (r.message) {
									frappe.set_route('Form', 'Farm Task Execution', r.message);
								}
							}
						});
					},
					__('Create Execution'),
					__('Create')
				);
			}, __('Actions'));
		}
	}
});


