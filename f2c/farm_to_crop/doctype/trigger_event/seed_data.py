# Copyright (c) 2025, Orgatek and contributors
# For license information, please see license.txt

import frappe


def seed_trigger_event():
	"""Seed trigger events with predefined common trigger events for farm management"""
	
	trigger_events = [
		# Time-based events
		{
			"trigger_type": "Time",
			"event_name": "Daily Irrigation Schedule",
			"description": "Automated daily irrigation schedule trigger based on time of day."
		},
		{
			"trigger_type": "Time",
			"event_name": "Weekly Crop Monitoring",
			"description": "Weekly scheduled trigger for crop monitoring and assessment."
		},
		{
			"trigger_type": "Time",
			"event_name": "Monthly Report Generation",
			"description": "Monthly automated report generation trigger."
		},
		{
			"trigger_type": "Time",
			"event_name": "Seasonal Planting Window",
			"description": "Trigger for optimal planting season based on calendar dates."
		},
		{
			"trigger_type": "Time",
			"event_name": "Harvest Deadline Reminder",
			"description": "Time-based reminder for approaching harvest deadlines."
		},
		
		# Condition-based events
		{
			"trigger_type": "Condition",
			"event_name": "Soil Moisture Below Threshold",
			"description": "Triggered when soil moisture levels fall below the minimum threshold."
		},
		{
			"trigger_type": "Condition",
			"event_name": "Temperature Exceeds Safe Range",
			"description": "Alert when temperature goes beyond safe growing conditions."
		},
		{
			"trigger_type": "Condition",
			"event_name": "Nutrient Deficiency Detected",
			"description": "Triggered when soil nutrient levels indicate deficiency."
		},
		{
			"trigger_type": "Condition",
			"event_name": "Pest Infestation Detected",
			"description": "Alert when pest presence exceeds acceptable levels."
		},
		{
			"trigger_type": "Condition",
			"event_name": "Disease Outbreak Alert",
			"description": "Triggered when disease symptoms are detected in crops."
		},
		
		# Behaviour-based events
		{
			"trigger_type": "Behaviour",
			"event_name": "Crop Growth Rate Anomaly",
			"description": "Triggered when crop growth rate deviates from expected patterns."
		},
		{
			"trigger_type": "Behaviour",
			"event_name": "Unusual Field Activity Pattern",
			"description": "Alert for unusual patterns in field activity or access."
		},
		{
			"trigger_type": "Behaviour",
			"event_name": "Equipment Usage Pattern Change",
			"description": "Triggered when equipment usage patterns show significant changes."
		},
		
		# Sensor-based events
		{
			"trigger_type": "Sensor",
			"event_name": "Soil Moisture Sensor Alert",
			"description": "IoT sensor-based alert for soil moisture levels."
		},
		{
			"trigger_type": "Sensor",
			"event_name": "Temperature Sensor Alert",
			"description": "Temperature sensor reading exceeds configured thresholds."
		},
		{
			"trigger_type": "Sensor",
			"event_name": "Humidity Sensor Alert",
			"description": "Humidity sensor detects conditions outside optimal range."
		},
		{
			"trigger_type": "Sensor",
			"event_name": "pH Sensor Alert",
			"description": "Soil pH sensor detects levels outside acceptable range."
		},
		
		# Weather-based events
		{
			"trigger_type": "Weather",
			"event_name": "Rainfall Alert",
			"description": "Weather-based trigger for rainfall predictions or occurrences."
		},
		{
			"trigger_type": "Weather",
			"event_name": "Frost Warning",
			"description": "Alert for predicted frost conditions that may damage crops."
		},
		{
			"trigger_type": "Weather",
			"event_name": "Heat Wave Alert",
			"description": "Warning for extreme heat conditions affecting crop health."
		},
		{
			"trigger_type": "Weather",
			"event_name": "Drought Condition Alert",
			"description": "Alert for prolonged dry conditions requiring intervention."
		},
		
		# Stage-based events
		{
			"trigger_type": "Stage",
			"event_name": "Germination Complete",
			"description": "Triggered when crop reaches germination stage completion."
		},
		{
			"trigger_type": "Stage",
			"event_name": "Flowering Stage Reached",
			"description": "Alert when crop enters flowering stage requiring specific care."
		},
		{
			"trigger_type": "Stage",
			"event_name": "Maturity Stage Alert",
			"description": "Notification when crop reaches maturity stage."
		},
		{
			"trigger_type": "Stage",
			"event_name": "Harvest Ready Notification",
			"description": "Alert indicating crop is ready for harvest."
		},
		
		# Threshold-based events
		{
			"trigger_type": "Threshold",
			"event_name": "Soil pH Out of Range",
			"description": "Triggered when soil pH levels exceed optimal range."
		},
		{
			"trigger_type": "Threshold",
			"event_name": "Nutrient Level Critical",
			"description": "Alert when nutrient levels reach critical thresholds."
		},
		{
			"trigger_type": "Threshold",
			"event_name": "Water Level Low",
			"description": "Triggered when water reservoir levels are critically low."
		},
		{
			"trigger_type": "Threshold",
			"event_name": "Pest Count Exceeds Limit",
			"description": "Alert when pest population exceeds acceptable threshold."
		},
		
		# Schedule-based events
		{
			"trigger_type": "Schedule",
			"event_name": "Planting Season Start",
			"description": "Scheduled trigger for the beginning of planting season."
		},
		{
			"trigger_type": "Schedule",
			"event_name": "Fertilization Schedule",
			"description": "Scheduled trigger for regular fertilization activities."
		},
		{
			"trigger_type": "Schedule",
			"event_name": "Pruning Schedule",
			"description": "Regularly scheduled pruning activities trigger."
		},
		
		# Alert-based events
		{
			"trigger_type": "Alert",
			"event_name": "Disease Detection Alert",
			"description": "Immediate alert when crop diseases are detected."
		},
		{
			"trigger_type": "Alert",
			"event_name": "Pest Infestation Alert",
			"description": "Urgent alert for pest infestation requiring immediate action."
		},
		{
			"trigger_type": "Alert",
			"event_name": "Equipment Maintenance Due",
			"description": "Reminder alert for scheduled equipment maintenance."
		},
		{
			"trigger_type": "Alert",
			"event_name": "Field Inspection Required",
			"description": "Alert indicating field inspection is needed."
		},
		
		# Manual events
		{
			"trigger_type": "Manual",
			"event_name": "Field Visit Scheduled",
			"description": "User-initiated trigger for scheduled field visits."
		},
		{
			"trigger_type": "Manual",
			"event_name": "Custom Activity Trigger",
			"description": "Manually triggered custom farm activity."
		},
		{
			"trigger_type": "Manual",
			"event_name": "Emergency Intervention",
			"description": "Manual trigger for emergency farm interventions."
		},
		
		# System events
		{
			"trigger_type": "System",
			"event_name": "Data Sync Complete",
			"description": "System trigger when data synchronization is completed."
		},
		{
			"trigger_type": "System",
			"event_name": "Backup Completed",
			"description": "System-generated trigger after successful backup."
		},
		{
			"trigger_type": "System",
			"event_name": "Report Generation Complete",
			"description": "System trigger when automated reports are generated."
		},
		
		# Location-based events
		{
			"trigger_type": "Location",
			"event_name": "Field Entry Detection",
			"description": "GPS/geo-fencing trigger when entering a field boundary."
		},
		{
			"trigger_type": "Location",
			"event_name": "Field Exit Detection",
			"description": "Triggered when exiting a field boundary."
		},
		{
			"trigger_type": "Location",
			"event_name": "Equipment Location Alert",
			"description": "Alert when equipment moves outside designated area."
		},
		
		# Integration-based events
		{
			"trigger_type": "Integration",
			"event_name": "Weather API Update",
			"description": "Trigger from external weather service API updates."
		},
		{
			"trigger_type": "Integration",
			"event_name": "Market Price Update",
			"description": "Trigger from market price API for crop pricing updates."
		},
		{
			"trigger_type": "Integration",
			"event_name": "Third-Party Sensor Data",
			"description": "Trigger from integrated third-party sensor systems."
		}
	]
	
	for event_data in trigger_events:
		# Check if trigger event already exists
		existing = frappe.db.get_value(
			"Trigger Event",
			{"event_name": event_data["event_name"], "trigger_type": event_data["trigger_type"]},
			"name"
		)
		
		if not existing:
			doc = frappe.get_doc({
				"doctype": "Trigger Event",
				**event_data
			})
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
			print(f"Created Trigger Event: {event_data['event_name']} ({event_data['trigger_type']})")
		else:
			print(f"Trigger Event already exists: {event_data['event_name']} ({event_data['trigger_type']})")


if __name__ == "__main__":
	frappe.connect()
	seed_trigger_event()

