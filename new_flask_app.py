from flask import Flask, request, jsonify, Response, make_response
import threading
import uuid
import csv
import io
import sqlite3
import datetime
import logging
import pytz

app = Flask(__name__)

# Logging
logging.basicConfig( # Basic logging structure for all the mishaps that may occur 
    level=logging.INFO, 
    format='%(asctime)s %(levelname)s: %(message)s'
)

# In-memory store for reports.
reports = {} # Dictionary for reports
reports_lock = threading.Lock() # Thread lock for handling unpredictiveness (Possibility of Edge Case?)

def dbconnect(): # Connects to sql database
    try:
        conn = sqlite3.connect('store_monitoring.db') # Try connecting to the db
        return conn # Output as return if it succeeds
    except sqlite3.Error as e:
        msg = f"Couldn't connect to the database: {e}"
        logging.error(msg) # Log as error, function call to logging
        raise sqlite3.Error(msg)
def getz(store_id):
    try:
        conn = dbconnect()
        cur = conn.cursor()
        cur.execute("SELECT timezone_str FROM timezones WHERE store_id=?", (store_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
        else:
            logging.info("No timezone found for store '%s'. Assuming America/Chicago.", store_id)
            return "America/Chicago"
    except Exception as e:
        logging.error("Error fetching timezone for store '%s': %s", store_id, e)
        return "America/Chicago"

def calctime(store_id, cs, ce): # Calculation of uptime or downtime 
    # cs = chain start
    # ce = chain end
    try:
        conn = dbconnect() # Call the function db connect
        cur = conn.cursor() # New cursor object for sqlite3
    except Exception as e:
        logging.error("Failed to get DB connection in calculation of uptime and downtime ,calctime: %s", e) # log this issue by calling logging
        return 0, 0

    try: # To get the last event
        cur.execute(
            "SELECT timestamp_utc, status FROM store_status WHERE store_id=? AND timestamp_utc < ? ORDER BY timestamp_utc DESC LIMIT 1",
            (store_id, cs.strftime("%Y-%m-%d %H:%M:%S")) # Format the sql o/p as our needs
        )
        row = cur.fetchone() # Iterates over the sqlite elements till no element with the id is present
        if row:
            inic = row[1] # Take the 2nd value, ie; its status (active/inactive)
        else:
            inic = 'active' # Assume event is active 24/7 if there is no event
    except sqlite3.Error as e:
        logging.error("SQL error in calctime while fetching prior event for store '%s': %s", store_id, e) # log this error
        conn.close() # Close connection
        return 0, 0

    # Get events within the chain.
    try:
        cur.execute( # execute the following query in sqlite3 db
            "SELECT timestamp_utc, status FROM store_status WHERE store_id=? AND timestamp_utc BETWEEN ? AND ? ORDER BY timestamp_utc ASC",
            (store_id, cs.strftime("%Y-%m-%d %H:%M:%S"), ce.strftime("%Y-%m-%d %H:%M:%S"))
        )
        events = cur.fetchall() # Fetch all the said events
    except sqlite3.Error as e:
        logging.error("SQL error in calctime while fetching events for store '%s': %s", store_id, e) # Log the event by calling logging
        conn.close()
        return 0, 0

    conn.close()
    timeline = [] # List for allocating all the states 
    timeline.append((cs, inic)) # Append initial chain to chain start
    for i in events: # Loop for parsing the events 
        try:
            event_time = datetime.datetime.strptime(i[0], "%Y-%m-%d %H:%M:%S")  # date-time, the first element in a event, format 
            timeline.append((event_time, i[1])) # Append events if criteria is met
        except Exception as e:
            logging.error("Error parsing event timestamp for store '%s': %s", store_id, e)
            continue
    timeline.append((ce, None))  # End marker; state is not used.

    uptime = 0
    downtime = 0

    # Parse chain, summating the durations
    for i in range(len(timeline) - 1):
        start, state = timeline[i] # start time 
        end = timeline[i + 1][0] # End time
        duration = (end - start).total_seconds() # total duration in seconds, end - start = duration
        if state == 'active':
            uptime += duration # increment the uptime by duration
        else:
            downtime += duration # increment the down time if its inactive

    return uptime, downtime

def gencsv(store_id):
    try:
        conn = dbconnect() # call function to connect to db
        cur = conn.cursor() # make a new cursor for parsing sqlite3
    except Exception as e:
        logging.error("DB error in generation of csv: %s", e) # Log this error
        return None

    try:
        cur.execute("SELECT MAX(timestamp_utc) FROM store_status WHERE store_id=?", (store_id,)) # get latest timestamp
        row = cur.fetchone() # iterates till none in db
    except sqlite3.Error as e:
        logging.error("SQL error while fetching latest timestamp for store '%s': %s", store_id, e) # log by calling the function
        conn.close() # close connection after displaying error
        return None

    try:
        if row and row[0]: # check the elements in row and the 1st element of row
            reftime = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") # get 1st element and format it 
            # reftime = reference time
        else:
            reftime = datetime.datetime.utcnow() # if not? it'll take the current universal time
            logging.info("No events found for store '%s'. Using current time as reference.", store_id) # log this too
    except Exception as e:
        logging.error("Error parsing timestamp for store '%s': %s", store_id, e) # Log this error but keep refrence time to the current universal time
        reftime = datetime.datetime.utcnow()
    conn.close() # Close this connection
    # Get the store's timezone; if missing, default to America/Chicago.
    tz_str = getz(store_id)
    try:
        local_tz = pytz.timezone(tz_str)
        # Convert reftime from UTC to the store's local time
        reftime_local = reftime.replace(tzinfo=pytz.utc).astimezone(local_tz).replace(tzinfo=None)
    except Exception as e:
        logging.error("Error converting reftime to local timezone for store '%s': %s", store_id, e)
        reftime_local = reftime

    hrstart = reftime - datetime.timedelta(hours=1) # start chain by hours
    daystart = reftime - datetime.timedelta(days=1) # start chain by day
    weekstart = reftime - datetime.timedelta(weeks=1) # start chain by week

    # Calculate up-down time in seconds
    uphrsec, dwhrsec = calctime(store_id, hrstart, reftime)  # hour
    updaysec, dwdaysec = calctime(store_id, daystart, reftime) # day
    upweeksec, dwweeksec = calctime(store_id, weekstart, reftime) # week

    # round up everything to 2 decimals 
    # 1hr = 3600 secs
    uphrmin = round(uphrsec / 60.0, 2) # (for hours) uptime hours -> mins 
    dwhrmin = round(dwhrsec / 60.0, 2)  # (for hours) downtime hours -> mins 
    updaymin = round(updaysec / 3600.0, 2) # (for days) uptime days -> hours
    dwdayhr = round(dwdaysec / 3600.0, 2)  # (for days) downtime days -> hours
    upweekhr = round(upweeksec / 3600.0, 2) # (for week) uptime week -> hours 
    dwweekhr = round(dwweeksec / 3600.0, 2) # (for week) downtime week -> hours

    # Making CSV? like, making the structure that gets appended to the csv
    output = io.StringIO() # For dynamic writing abilities, store in mem instead of disk
    writer = csv.writer(output) 
    writer.writerow([  
        "store_id", "uptime_last_hour(min)", "uptime_last_day(hrs)",
        "uptime_last_week(hrs)", "downtime_last_hour(min)",
        "downtime_last_day(hrs)", "downtime_last_week(hrs)"
    ])  # Write this whole thing as the benchmark legends
    writer.writerow([
        store_id, uphrmin, updaymin, upweekhr,
        dwhrmin, dwdayhr, dwweekhr
    ]) # Actual data

    return output.getvalue()  # Output the csv

def storerep(repid, store_id, repdata): # Was this necessary ? May be for company's reference?
    try:
        conn = dbconnect()
        cur = conn.cursor()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO reports (report_id, store_id, repdata, generated_at) VALUES (?,?,?,?)",(repid, store_id, repdata, now))
        conn.commit()
        conn.close()
        logging.info("Report %s stored in the database.", repid)
    except Exception as e:
        logging.error("Error storing report %s for store %s: %s", repid, store_id, e)
        
def fetchrep(repid):
    try:
        conn = dbconnect()
        cur = conn.cursor()
        cur.execute("SELECT repdata FROM reports WHERE report_id=?", (repid,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0]
        else:
            return None
    except Exception as e:
        logging.error("Error fetching report %s from database: %s", repid, e)
        return None

def buildrep(repid, store_id): # to build the csv
    try:
        with reports_lock: # Lock cpu for one process (edge case?)
            reports[repid]['state'] = 'Running' 
        logging.info("Report %s for store '%s' is now running.", repid, store_id) # Log this too, imp**
        repdata = gencsv(store_id) # call function
        if repdata is None: # If it returns None
            raise Exception("CSV generation failed (returned None).") 
        with reports_lock: # lock report to ensure only one process access the db
            reports[repid]['repdata'] = repdata # Dictionary with key report report ID and object report data = report data
            reports[repid]['state'] = 'Complete' # Mark it as complete if done
        storerep(repid, store_id, repdata)
        logging.info("Report %s for store '%s' completed successfully.", repid, store_id) # Log it as success
    except Exception as e:
        error_msg = f"Error processing report {repid} for store '{store_id}': {e}" # Processing error
        logging.error(error_msg)
        with reports_lock:
            reports[repid]['state'] = 'Error' 
            reports[repid]['repdata'] = None # Error gives out None datatype
            
@app.route('/trigger_report', methods=['GET']) # /trigger_report api route , allows GET method
def trigger_report():
    store_id = request.args.get('store_id') # get the store_id
    if not store_id:
        msg = "Forgot to include 'store_id' in your request?"
        logging.error(msg) # call the logging function
        return jsonify({"error": msg}), 400 # Conversion to json for the message transfer over http 
    # 400 is bad request

    try:
        repid = str(uuid.uuid4()) # assigning unique id for every report
    except Exception as e:
        msg = f"Could not generate a report ID: {e}"  # Generation of ID failed
        logging.error(msg) # Important message to log
        return jsonify({"error": msg}), 500 # Not soo unexpected(edgecase handled?) condition, not able to process. Any edge case failure will give this

    try:
        with reports_lock: # Lock for avoiding race conditions
            reports[repid] = {'store_id': store_id, 'state': 'Pending', 'repdata': None} # Dictionary with empty data field
    except Exception as e:
        msg = f"Could not save report metadata for report {repid}: {e}"
        logging.error(msg) # Imp logging
        return jsonify({"error": msg}), 500 # Not soo unexpected(edgecase handled?) condition

    try:
        thread = threading.Thread(target=buildrep, args=(repid, store_id)) # Building report, assign it to a thread
        thread.start() # Start building the report 
    except Exception as e:
        msg = f"Failed to start report processing for report {repid}: {e}" 
        logging.error(msg) # Log the failure of report processing
        return jsonify({"error": msg}), 500 # Not soo unexpected(edgecase handled?) condition 

    logging.info("Report %s triggered successfully for store '%s'.", repid, store_id) # Trigger process successful
    return jsonify({"repid": repid}) # Return the id over http

@app.route('/get_report', methods=['GET']) # API route /get_report that allows GET report
def get_report():
    repid = request.args.get('repid') # get the ID of the report
    if not repid: # if the ID is missing
        msg = "The ID of the report is missing from the request." 
        logging.error(msg) # Log this error
        return jsonify({"error": msg}), 400 # Bad request

    repdata = fetchrep(repid)
    if repdata is None:
        msg = f"We couldn't find a stored report with ID '{repid}'."
        logging.error(msg)
        return jsonify({"error": msg}), 404

    try:
        response = make_response(repdata)
        response.headers["Content-Type"] = "text/csv"
        response.headers["Content-Disposition"] = 'attachment; filename="report.csv"'
        logging.info("Returning stored CSV for report %s.", repid)
        return response
    except Exception as e:
        msg = f"Failed to create CSV response for report {repid}: {e}"
        logging.error(msg)
        return jsonify({"error": msg}), 500

if __name__ == '__main__':
    try:
        logging.info(" Server running on 192.168.0.107:8001")
        app.run(host='192.168.0.107', port=8001, debug=True) # apply it on the ip, port 
    except Exception as e:
        logging.critical("Engaging Flask server failed: %s", e) # Important log, "it didnt start"
