"""AutomationSchedule model — PLANNED, not yet implemented.

Planned fields (see docs/database-design.md):
- outlet_id: foreign key to smart_outlets.id
- name: schedule display name
- start_time, end_time
- enabled: on/off switch
- days / recurrence: which weekdays the schedule applies to

The schedule engine will live in app/services/, not in this model.
"""
