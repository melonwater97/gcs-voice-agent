// This capability is never serialized, returned to tools, or accepted from HTTP input.
export const flightAuthority = Symbol('flight-ui-approval');
export const protectedFlightTools = new Set(['execute_authorized_action']);
