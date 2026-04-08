"""Shared constants for logistics flows (LTT, planner, pickup)."""

# Machinery types allowed as LTT transport vehicle and for planner "transport asset" resolution.
TRANSPORT_MACHINERY_TYPES: tuple[str, ...] = (
	"Vehicle",
	"Tractor",
	"Earthmoving",
	"Harvester",
	"Combine Harvester",
	"Thresher",
)
