import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime


@frappe.whitelist()
def get_logistics_tickets(transfer_type=None, status=None, from_warehouse=None,
                          date_from=None, date_to=None, limit=500):
    """
    Return Logistics Transfer Tickets filtered by transfer_type (Internal/External),
    status, warehouse, and date range.
    """
    filters = []

    if transfer_type and transfer_type != 'all':
        filters.append(['transfer_type', '=', transfer_type])

    if status and status != 'all':
        filters.append(['status', '=', status])

    if from_warehouse:
        filters.append(['from_warehouse', '=', from_warehouse])

    if date_from:
        filters.append(['creation', '>=', date_from + ' 00:00:00'])

    if date_to:
        filters.append(['creation', '<=', date_to + ' 23:59:59'])

    tickets = frappe.get_list(
        'Logistics Transfer Ticket',
        filters=filters,
        fields=[
            'name', 'transfer_type', 'status', 'from_warehouse', 'to_warehouse',
            'from_location', 'to_location', 'farm_task_execution',
            'stock_entry', 'asset_movement',
            'dispatched_on', 'received_on', 'report_reason',
            'creation', 'modified'
        ],
        limit_page_length=int(limit),
        order_by='modified desc'
    )

    return tickets


@frappe.whitelist()
def resolve_reported_ticket(ticket_name, resolution_type, notes=None, planned_pickup_on=None):
    """
    Resolve a Reported ticket.
    resolution_type:
      - 'solved'      → move to Received (mark as delivered)
      - 'reschedule'  → move back to Pending Pickup (restart the pickup).
        Optional planned_pickup_on: datetime string for when pickup is planned (e.g. ISO or YYYY-MM-DD HH:mm:ss).
    """
    ticket = frappe.get_doc('Logistics Transfer Ticket', ticket_name)

    if ticket.status != 'Reported':
        frappe.throw(_('Ticket {0} is not in Reported status (current: {1})').format(
            ticket_name, ticket.status))

    if resolution_type == 'solved':
        ticket.status = 'Received'
        ticket.received_on = now_datetime()
        if notes:
            ticket.report_reason = (ticket.report_reason or '') + '\n[Resolved - Solved]: ' + notes
        ticket.save(ignore_permissions=True)
        frappe.db.commit()
        return {'status': 'Received', 'message': 'Ticket marked as Received (Solved).'}

    elif resolution_type == 'reschedule':
        ticket.status = 'Pending Pickup'
        ticket.dispatched_on = None
        if planned_pickup_on:
            ticket.planned_pickup_on = get_datetime(planned_pickup_on)
        if notes:
            ticket.report_reason = (ticket.report_reason or '') + '\n[Resolved - Rescheduled]: ' + notes
        ticket.save(ignore_permissions=True)
        frappe.db.commit()
        return {'status': 'Pending Pickup', 'message': 'Ticket rescheduled to Pending Pickup.'}

    else:
        frappe.throw(_('Invalid resolution_type: {0}. Use "solved" or "reschedule".').format(resolution_type))


@frappe.whitelist()
def abort_reported_ticket(ticket_name, abort_type, notes=None):
    """
    Abort a Reported ticket.
    abort_type:
      - 'replace' → Cancel this ticket (driver replaces the reported task)
      - 'resume'  → Move back to In Transit (resume from reported section)
    """
    ticket = frappe.get_doc('Logistics Transfer Ticket', ticket_name)

    if ticket.status != 'Reported':
        frappe.throw(_('Ticket {0} is not in Reported status (current: {1})').format(
            ticket_name, ticket.status))

    if abort_type == 'replace':
        ticket.status = 'Cancelled'
        if notes:
            ticket.report_reason = (ticket.report_reason or '') + '\n[Aborted - Replace]: ' + notes
        ticket.save(ignore_permissions=True)
        frappe.db.commit()
        return {'status': 'Cancelled', 'message': 'Ticket cancelled (replace task).'}

    elif abort_type == 'resume':
        ticket.status = 'In Transit'
        if notes:
            ticket.report_reason = (ticket.report_reason or '') + '\n[Aborted - Resume]: ' + notes
        ticket.save(ignore_permissions=True)
        frappe.db.commit()
        return {'status': 'In Transit', 'message': 'Ticket resumed to In Transit.'}

    else:
        frappe.throw(_('Invalid abort_type: {0}. Use "replace" or "resume".').format(abort_type))


@frappe.whitelist()
def get_warehouse_location_coords(warehouse):
    """
    Return GPS coordinates for a warehouse via its linked Geo Fencing Area.
    Returns { lat, lng, location_name } or None.
    """
    try:
        from f2c.inventory.logistics_transfer_ticket_api import get_location_for_warehouse
        location_result = get_location_for_warehouse(warehouse)
        location_name = location_result.get('location') if location_result else None

        if not location_name:
            return None

        location_doc = frappe.get_doc('Location', location_name)
        if location_doc and location_doc.latitude and location_doc.longitude:
            return {
                'lat': location_doc.latitude,
                'lng': location_doc.longitude,
                'location_name': location_name
            }
    except Exception:
        pass

    return None


@frappe.whitelist()
def get_ltt_location_coords_batch(ticket_names):
    """
    Return pickup and drop-off coordinates for multiple LTTs.
    ticket_names: list of Logistics Transfer Ticket names.
    Returns: { ticket_name: { "pickup": { "lat", "lng" } | null, "drop_off": { "lat", "lng" } | null }, ... }
    """
    from frappe.utils import flt

    if not ticket_names:
        return {}
    if isinstance(ticket_names, str):
        ticket_names = frappe.parse_json(ticket_names) or []
    result = {}
    for name in ticket_names:
        if not name:
            continue
        try:
            doc = frappe.get_doc("Logistics Transfer Ticket", name)
        except Exception:
            result[name] = {"pickup": None, "drop_off": None}
            continue
        pickup = None
        drop_off = None
        from_lt = (doc.from_location_type or "Warehouse").strip()
        to_lt = (doc.to_location_type or "Warehouse").strip()
        if from_lt == "Warehouse" and doc.from_warehouse:
            wh_coords = get_warehouse_location_coords(doc.from_warehouse)
            if wh_coords and wh_coords.get("lat") is not None and wh_coords.get("lng") is not None:
                pickup = {"lat": float(wh_coords["lat"]), "lng": float(wh_coords["lng"])}
        else:
            lat, lng = getattr(doc, "from_latitude", None), getattr(doc, "from_longitude", None)
            if lat is not None and lng is not None and (flt(lat) != 0 or flt(lng) != 0):
                pickup = {"lat": float(lat), "lng": float(lng)}
        if to_lt == "Warehouse" and doc.to_warehouse:
            wh_coords = get_warehouse_location_coords(doc.to_warehouse)
            if wh_coords and wh_coords.get("lat") is not None and wh_coords.get("lng") is not None:
                drop_off = {"lat": float(wh_coords["lat"]), "lng": float(wh_coords["lng"])}
        else:
            lat, lng = getattr(doc, "to_latitude", None), getattr(doc, "to_longitude", None)
            if lat is not None and lng is not None and (flt(lat) != 0 or flt(lng) != 0):
                drop_off = {"lat": float(lat), "lng": float(lng)}
        result[name] = {"pickup": pickup, "drop_off": drop_off}
    return result
