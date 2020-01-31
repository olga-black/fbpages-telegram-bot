"""
Get upcoming events for page 
If there are upcoming events, write them to json
Check every 5 min for changes in events and write changes to json
Create job of 1h before event start_time
Send notification at that time
Mark event as notified about
Rewrite json when event no longer is in upcoming
"""
import json
import facebook
import telegram

def get_upcoming_events(pages):
	events_dict = {}
	for page in facebook_pages:
	    events_dict[page] = graph.get_object(id=f'{page}/events?time_filter=upcoming')
	return(events_dict)

def check_if_notified(event_id):
	pass

def write_event_to_json(event_id, file):

	pass


def notify_about_event():
	pass

