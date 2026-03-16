frappe.ui.form.on('Hand Tool', {
	refresh(frm) {
		hide_unwanted_sections(frm);
		// Run again after a delay to catch sections rendered dynamically (e.g. from specs_json/custom fields)
		setTimeout(() => hide_unwanted_sections(frm), 100);
		setTimeout(() => hide_unwanted_sections(frm), 500);
	}
});

function hide_unwanted_sections(frm) {
	if (!frm || !frm.wrapper) return;
	// Hide sections so only planned ones show: Basic Info, Images, Asset Info, Equipment documents, Tools & Accessories Specs, Additional
	frm.wrapper.find('.form-section').each(function () {
		const $sec = $(this);
		if ($sec.hasClass('hide-control')) return;
		const label = $sec.find('.section-head').first().text().trim();
		const should_hide =
			label === __('General') ||
			label === 'General' ||
			label.indexOf(__('Specifications (Dynamic)')) !== -1 ||
			label.indexOf('Specifications (Dynamic)') !== -1 ||
			(label.toLowerCase().indexOf('specifications') !== -1 && label.toLowerCase().indexOf('dynamic') !== -1);
		if (should_hide) {
			$sec.addClass('hide-control').hide();
		}
	});
}


