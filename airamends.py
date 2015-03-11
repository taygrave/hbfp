from flask import Flask, render_template, request, g, flash, redirect, url_for
from flask import session as flask_session
from flask.ext.login import LoginManager
from flask.ext.login import login_user, logout_user, current_user
import pdb, json, re
from datetime import datetime
from model import *
import gmailapiworks, seed_flights
from apiclient.discovery import build_from_document
import httplib2
from oauth2client.client import OAuth2WebServerFlow, AccessTokenCredentials
from flask import jsonify
import config 

app = Flask(__name__)
app.config.from_pyfile('./config.py')
login_manager = LoginManager()
login_manager.init_app(app)

def get_api(credentials):
    http_auth = credentials.authorize(httplib2.Http())
    doc = open("discovery.json")
    gmail_service = build_from_document(doc.read(), http = http_auth)
    return gmail_service

def get_auth_flow():
    auth_flow = OAuth2WebServerFlow(
        client_id = config.GMAIL_CLIENT_ID,
        client_secret = config.GMAIL_CLIENT_SECRET,
        scope = config.GMAIL_AUTH_SCOPE,
        redirect_uri = url_for('login_callback', _external = True))
    return auth_flow

@login_manager.user_loader
def load_user(userid):
    return User.query.get(int(userid))

@app.before_request
def before_request():
    g.carbon_price = 37.00 #Official White House SCC as of Nov 2014
    if current_user.is_authenticated():
        # print "before request getting credentials"
        credentials = AccessTokenCredentials(current_user.access_token, u'')
        # print "before request, retreived credentials"
        g.gmail_api = get_api(credentials)
        # print "before request, built service"

    if flask_session.get('user_id') == None:
        g.status = "Log In"
        g.link = "/login/"
    else:
        #TODO - directly add email to session once retrieved instead of querying db for it each time
        g.status = current_user.email
        g.link = "/logout/"

@app.route("/")
def homepage():
    return render_template("index.html")

@app.route("/flights.js")
def flights4map():
    """Queries db for all flights and turns into a json for mapbox animation"""
    total_flights = Flight.query.all()
    map_list = []

    for flight in total_flights:
        lat_D = flight.departure.latitude
        long_D = flight.departure.longitude
        lat_A = flight.arrival.latitude
        long_A = flight.arrival.longitude
        
        map_list.append([[lat_D,long_D],[lat_A,long_A]])

    str_coords = json.dumps(map_list)
    return str_coords

@app.route("/airports.js")
def get_airports(format="json"):
    """Queries db for airport info and turns code and city pairs in json (default) for user flight adding info. If any other argument passed (eg. 'python') will return python list object."""
    airports = Airport.query.all()
    airport_list = []

    for obj in airports:
        air_str = '%s (%s)' %(obj.city, obj.id)
        airport_list.append(air_str)

    if format == "json":
        str_airports = json.dumps(airport_list)
        return str_airports

    else:
        return airport_list

@app.route("/map")
def make_map():
    """Provides view of animated flight paths using user's flight db info"""
    json_array = flights4map()
    return render_template("map.html", jsonarray=json_array)

@app.route("/getflights", methods=["GET"])
def getflights():
    emails_in_db = Email.query.filter(Email.user_id == current_user.id).all()
    if emails_in_db == None:
        gmailapiworks.add_msgs_to_db(g.gmail_api, current_user.id)
        emails_in_db = Email.query.filter(Email.user_id == current_user.id).all()

    email_stats = [len(list(emails_in_db)), emails_in_db[-1].date, emails_in_db[0].date]

    flights_in_db = Flight.query.filter(Flight.user_id == current_user.id).all()
    if flights_in_db == [] or None:
        user_flights = seed_flights.seed_flights()
        CO2e = seed_flights.CO2e_results(user_flights)
    else: 
        user_flights = Flight.query.all()
        CO2e = seed_flights.CO2e_results(user_flights)

    years_list = seed_flights.report_by_year()

    return render_template("/getflights.html", email_stats=email_stats, user_flights=user_flights, CO2e=CO2e, years_list=years_list)

@app.route("/flight_reset", methods=["POST"])
def reset_flights():
    Flight.query.delete()
    session.commit()
    return redirect(url_for('getflights'))

@app.route("/complete_reset", methods=["POST"])
def complete_reset():
    Email.query.delete()
    Flight.query.delete()
    session.commit()
    return redirect(url_for('getflights'))

@app.route("/getflights/<year>")
def yearflights(year):
    year = int(year)
    results_list = []
    user_flights = Flight.query.order_by(asc(Flight.date)).all()
    
    working_list = [obj for obj in user_flights if (obj.date.year == year)]

    for flight in working_list:
        #rounding completed here removes some precision, and also removes precision error
        CO2e = round(seed_flights.calc_carbon((flight.depart, flight.arrive)),2)
        #using a backreference here to name the cities for display instead of using their airport codes, for better user recognition
        #TODO consider returning airport codes as well
        date = flight.date.strftime('%b-%d')
        depart = "%s (%s)" %(flight.departure.city, flight.depart)
        arrive = "%s (%s)" %(flight.arrival.city, flight.arrive)

        results_list.append((date, depart, arrive, CO2e, flight.id))

    airports_json = get_airports()

    return render_template("/yearflights.html", year=year, results_list=results_list, airports_json=airports_json)

@app.route("/delete_flight", methods=["POST"])
def delete_flight():
    id = int(request.values['id'])
    flight = Flight.query.filter_by(id = id).one()
    session.delete(flight)
    session.commit()
    return "OK"

@app.route("/add_flight", methods=["GET"])
def add_flight():
    """Receives user input for flight details (date, departure, arrival) and makes a new flight entry in the db for user"""
    date = request.args.get('purchase_date')
    depart = request.args.get('depart')
    arrive = request.args.get('arrive')
    airport_list = get_airports(format="python")

    if (arrive in airport_list) and (depart in airport_list):
        #Set up flight to be added to the db
        user_id = flask_session.get('user_id')
        db_date = datetime.strptime(date, "%Y-%m-%d")
        db_depart = re.search((r"([A-Z]{3})"),depart).group()
        db_arrive = re.search((r"([A-Z]{3})"),arrive).group()
        entry = Flight(user_id=user_id, email_id=0, date=db_date, depart=db_depart, arrive=db_arrive) #special email_id code of "0" used to indicate manual user added flight
        session.add(entry)
        session.commit()
        
        #Return info for table addition
        date = db_date.strftime('%b-%d')
        CO2e = round(seed_flights.calc_carbon((db_depart,db_arrive)),2)
        price = CO2e * g.carbon_price
        
        return jsonify(date=date,
            depart=depart,
            arrive=arrive,
            CO2e=CO2e,
            price=price,
            id=entry.id)

    else:
        return "Error"

@app.route("/aboutcalc")
def aboutcalc():
    return render_template("carboncalcs.html")

@app.route('/login/')
def login():
    if current_user.is_authenticated():
        return redirect(url_for('homepage'))
    else:
        auth_flow = get_auth_flow()
        auth_uri = auth_flow.step1_get_authorize_url()
        return redirect(auth_uri)

@app.route('/login/callback/')
def login_callback():
    code = request.args.get('code')
    auth_flow = get_auth_flow()
    credentials = auth_flow.step2_exchange(code)
    gmail_api = get_api(credentials)
    gmail_user = gmail_api.users().getProfile(userId = 'me').execute()
    email = gmail_user['emailAddress']
    access_token = credentials.access_token

    user = User.query.filter_by(email = email).first()
    if user:
        user.access_token = access_token
        session.commit()

    else:
        user = User(email=email, access_token=access_token)
        user.save()
    
    login_user(user, remember = True)

    return redirect(url_for('homepage'))

@app.route('/logout/')
def logout():
    logout_user()
    return redirect(url_for('homepage'))

if __name__ == "__main__":
    app.run(debug = True)

