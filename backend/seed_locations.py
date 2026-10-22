"""
Seed the India Post directory used by the location picker.

Production loads the full India Post dump (the `pincodes` table). A fresh
local database has that table empty, so /api/locations/search returns no
results and the picker opens blank. This inserts the same major cities the
frontend keeps as its built-in fallback, so local development can pick an
area (and gets real lat/lng for the map).

Usage (from backend/, with the venv active):

    python seed_locations.py
"""
from app import create_app
from extensions import db
from models import Pincode

# (office_name, district, state, pincode, latitude, longitude)
LOCATIONS = [
    ('Chandigarh', 'Chandigarh', 'Chandigarh', '160001', 30.7333, 76.7794),
    ('Mohali', 'SAS Nagar', 'Punjab', '140001', 30.7046, 76.7179),
    ('Ludhiana', 'Ludhiana', 'Punjab', '141001', 30.901, 75.8573),
    ('Amritsar', 'Amritsar', 'Punjab', '143001', 31.634, 74.8723),
    ('Jalandhar', 'Jalandhar', 'Punjab', '144001', 31.326, 75.5762),
    ('Bathinda', 'Bathinda', 'Punjab', '151001', 30.211, 74.9455),
    ('Patiala', 'Patiala', 'Punjab', '147001', 30.3398, 76.3869),
    ('Pathankot', 'Pathankot', 'Punjab', '145001', 32.2643, 75.6421),
    ('Panchkula', 'Panchkula', 'Haryana', '134001', 30.6942, 76.8606),
    ('Ambala', 'Ambala', 'Haryana', '133001', 30.3752, 76.7821),
    ('Karnal', 'Karnal', 'Haryana', '132001', 29.6857, 76.9905),
    ('Faridabad', 'Faridabad', 'Haryana', '121001', 28.4089, 77.3178),
    ('Gurugram', 'Gurugram', 'Haryana', '122001', 28.4595, 77.0266),
    ('Hisar', 'Hisar', 'Haryana', '125001', 29.1492, 75.7217),
    ('Rohtak', 'Rohtak', 'Haryana', '124001', 28.8955, 76.6066),
    ('Shimla', 'Shimla', 'Himachal Pradesh', '171001', 31.1048, 77.1734),
    ('Rohru', 'Shimla', 'Himachal Pradesh', '171001', 31.2043, 77.7488),
    ('Theog', 'Shimla', 'Himachal Pradesh', '171001', 31.1236, 77.3444),
    ('Rampur Bushahr', 'Shimla', 'Himachal Pradesh', '172001', 31.4508, 77.635),
    ('Reckong Peo', 'Kinnaur', 'Himachal Pradesh', '172001', 31.5467, 78.2634),
    ('Kalpa', 'Kinnaur', 'Himachal Pradesh', '172001', 31.5397, 78.2586),
    ('Keylong', 'Lahaul & Spiti', 'Himachal Pradesh', '175001', 32.5711, 77.0299),
    ('Kaza', 'Lahaul & Spiti', 'Himachal Pradesh', '172001', 32.2246, 78.0714),
    ('Solan', 'Solan', 'Himachal Pradesh', '173001', 30.9045, 77.0967),
    ('Kasauli', 'Solan', 'Himachal Pradesh', '173001', 30.8985, 76.9653),
    ('Parwanoo', 'Solan', 'Himachal Pradesh', '173001', 30.8377, 76.9614),
    ('Nalagarh', 'Solan', 'Himachal Pradesh', '174001', 31.0503, 76.7213),
    ('Nahan', 'Sirmaur', 'Himachal Pradesh', '173001', 30.559, 77.2934),
    ('Paonta Sahib', 'Sirmaur', 'Himachal Pradesh', '173001', 30.4384, 77.625),
    ('Rajgarh', 'Sirmaur', 'Himachal Pradesh', '173001', 30.8347, 77.2903),
    ('Bilaspur', 'Bilaspur', 'Himachal Pradesh', '174001', 31.335, 76.757),
    ('Ghumarwin', 'Bilaspur', 'Himachal Pradesh', '174001', 31.4363, 76.7093),
    ('Una', 'Una', 'Himachal Pradesh', '174001', 31.4685, 76.2708),
    ('Santokhgarh', 'Una', 'Himachal Pradesh', '174001', 31.5167, 76.3167),
    ('Hamirpur', 'Hamirpur', 'Himachal Pradesh', '177001', 31.6862, 76.5217),
    ('Nadaun', 'Hamirpur', 'Himachal Pradesh', '177001', 31.7833, 76.35),
    ('Kangra', 'Kangra', 'Himachal Pradesh', '176001', 32.0998, 76.2691),
    ('Dharamshala', 'Kangra', 'Himachal Pradesh', '176001', 32.219, 76.3234),
    ('Palampur', 'Kangra', 'Himachal Pradesh', '176001', 32.1109, 76.5363),
    ('Nurpur', 'Kangra', 'Himachal Pradesh', '176001', 32.2985, 75.9014),
    ('Jawalamukhi', 'Kangra', 'Himachal Pradesh', '176001', 31.8734, 76.3236),
    ('Baijnath', 'Kangra', 'Himachal Pradesh', '176001', 32.05, 76.65),
    ('Mandi', 'Mandi', 'Himachal Pradesh', '175001', 31.708, 76.9318),
    ('Sundernagar', 'Mandi', 'Himachal Pradesh', '175001', 31.533, 76.904),
    ('Jogindernagar', 'Mandi', 'Himachal Pradesh', '175001', 31.988, 76.788),
    ('Kullu', 'Kullu', 'Himachal Pradesh', '175001', 31.9576, 77.1095),
    ('Manali', 'Kullu', 'Himachal Pradesh', '175001', 32.2432, 77.1892),
    ('Banjar', 'Kullu', 'Himachal Pradesh', '175001', 31.6367, 77.3417),
    ('Chamba', 'Chamba', 'Himachal Pradesh', '176001', 32.556, 76.126),
    ('Dalhousie', 'Chamba', 'Himachal Pradesh', '176001', 32.5387, 75.97),
    ('Bharmour', 'Chamba', 'Himachal Pradesh', '176001', 32.4472, 76.5347),
    ('Jammu', 'Jammu', 'Jammu & Kashmir', '180001', 32.7266, 74.857),
    ('Srinagar', 'Srinagar', 'Jammu & Kashmir', '190001', 34.0837, 74.7973),
    ('Dehradun', 'Dehradun', 'Uttarakhand', '248001', 30.3165, 78.0322),
    ('Haridwar', 'Haridwar', 'Uttarakhand', '249001', 29.9457, 78.1642),
    ('New Delhi', 'New Delhi', 'Delhi', '110001', 28.6139, 77.209),
    ('Noida', 'Gautam Buddh Nagar', 'Uttar Pradesh', '201001', 28.5355, 77.391),
    ('Ghaziabad', 'Ghaziabad', 'Uttar Pradesh', '245001', 28.6692, 77.4538),
    ('Meerut', 'Meerut', 'Uttar Pradesh', '250001', 28.9845, 77.7064),
    ('Agra', 'Agra', 'Uttar Pradesh', '282001', 27.1767, 78.0081),
    ('Mathura', 'Mathura', 'Uttar Pradesh', '281001', 27.4924, 77.6737),
    ('Aligarh', 'Aligarh', 'Uttar Pradesh', '202001', 27.8974, 78.088),
    ('Bareilly', 'Bareilly', 'Uttar Pradesh', '243001', 28.367, 79.4304),
    ('Lucknow', 'Lucknow', 'Uttar Pradesh', '226001', 26.8467, 80.9462),
    ('Kanpur', 'Kanpur Nagar', 'Uttar Pradesh', '208001', 26.4499, 80.3319),
    ('Varanasi', 'Varanasi', 'Uttar Pradesh', '221001', 25.3176, 82.9739),
    ('Prayagraj', 'Prayagraj', 'Uttar Pradesh', '211001', 25.4358, 81.8463),
    ('Gorakhpur', 'Gorakhpur', 'Uttar Pradesh', '273001', 26.7606, 83.3732),
    ('Jaipur', 'Jaipur', 'Rajasthan', '302001', 26.9124, 75.7873),
    ('Jodhpur', 'Jodhpur', 'Rajasthan', '342001', 26.2389, 73.0243),
    ('Udaipur', 'Udaipur', 'Rajasthan', '313001', 24.5854, 73.7125),
    ('Kota', 'Kota', 'Rajasthan', '324001', 25.2138, 75.8648),
    ('Ajmer', 'Ajmer', 'Rajasthan', '305001', 26.4499, 74.6399),
    ('Bikaner', 'Bikaner', 'Rajasthan', '334001', 28.0229, 73.3119),
    ('Ahmedabad', 'Ahmedabad', 'Gujarat', '380001', 23.0225, 72.5714),
    ('Surat', 'Surat', 'Gujarat', '395001', 21.1702, 72.8311),
    ('Vadodara', 'Vadodara', 'Gujarat', '390001', 22.3072, 73.1812),
    ('Rajkot', 'Rajkot', 'Gujarat', '360001', 22.3039, 70.8022),
    ('Gandhinagar', 'Gandhinagar', 'Gujarat', '382001', 23.2156, 72.6369),
    ('Bhopal', 'Bhopal', 'Madhya Pradesh', '462001', 23.2599, 77.4126),
    ('Indore', 'Indore', 'Madhya Pradesh', '452001', 22.7196, 75.8577),
    ('Gwalior', 'Gwalior', 'Madhya Pradesh', '474001', 26.2183, 78.1828),
    ('Jabalpur', 'Jabalpur', 'Madhya Pradesh', '482001', 23.1815, 79.9864),
    ('Raipur', 'Raipur', 'Chhattisgarh', '492001', 21.2514, 81.6296),
    ('Mumbai', 'Mumbai', 'Maharashtra', '400001', 19.076, 72.8777),
    ('Thane', 'Thane', 'Maharashtra', '401001', 19.2183, 72.9781),
    ('Navi Mumbai', 'Thane', 'Maharashtra', '410001', 19.033, 73.0297),
    ('Pune', 'Pune', 'Maharashtra', '411001', 18.5204, 73.8567),
    ('Nashik', 'Nashik', 'Maharashtra', '422001', 19.9975, 73.7898),
    ('Nagpur', 'Nagpur', 'Maharashtra', '440001', 21.1458, 79.0882),
    ('Aurangabad', 'Aurangabad', 'Maharashtra', '431001', 19.8762, 75.3433),
    ('Kolhapur', 'Kolhapur', 'Maharashtra', '416001', 16.705, 74.2433),
    ('Panaji', 'North Goa', 'Goa', '403001', 15.4909, 73.8278),
    ('Bengaluru', 'Bengaluru Urban', 'Karnataka', '560001', 12.9716, 77.5946),
    ('Mysuru', 'Mysuru', 'Karnataka', '570001', 12.2958, 76.6394),
    ('Mangaluru', 'Dakshina Kannada', 'Karnataka', '575001', 12.9141, 74.856),
    ('Hubballi', 'Dharwad', 'Karnataka', '580001', 15.3647, 75.124),
    ('Hyderabad', 'Hyderabad', 'Telangana', '500001', 17.385, 78.4867),
    ('Warangal', 'Warangal', 'Telangana', '506001', 17.9689, 79.5941),
    ('Visakhapatnam', 'Visakhapatnam', 'Andhra Pradesh', '530001', 17.6868, 83.2185),
    ('Vijayawada', 'Krishna', 'Andhra Pradesh', '520001', 16.5062, 80.648),
    ('Tirupati', 'Tirupati', 'Andhra Pradesh', '517001', 13.6288, 79.4192),
    ('Chennai', 'Chennai', 'Tamil Nadu', '600001', 13.0827, 80.2707),
    ('Coimbatore', 'Coimbatore', 'Tamil Nadu', '641001', 11.0168, 76.9558),
    ('Madurai', 'Madurai', 'Tamil Nadu', '625001', 9.9252, 78.1198),
    ('Tiruchirappalli', 'Tiruchirappalli', 'Tamil Nadu', '620001', 10.7905, 78.7047),
    ('Salem', 'Salem', 'Tamil Nadu', '636001', 11.6643, 78.146),
    ('Kochi', 'Ernakulam', 'Kerala', '682001', 9.9312, 76.2673),
    ('Thiruvananthapuram', 'Thiruvananthapuram', 'Kerala', '695001', 8.5241, 76.9366),
    ('Kozhikode', 'Kozhikode', 'Kerala', '673001', 11.2588, 75.7804),
    ('Thrissur', 'Thrissur', 'Kerala', '680001', 10.5276, 76.2144),
    ('Kolkata', 'Kolkata', 'West Bengal', '700001', 22.5726, 88.3639),
    ('Howrah', 'Howrah', 'West Bengal', '711001', 22.5958, 88.2636),
    ('Siliguri', 'Darjeeling', 'West Bengal', '734001', 26.7271, 88.3953),
    ('Durgapur', 'Paschim Bardhaman', 'West Bengal', '713001', 23.5204, 87.3119),
    ('Patna', 'Patna', 'Bihar', '800001', 25.5941, 85.1376),
    ('Gaya', 'Gaya', 'Bihar', '823001', 24.7914, 85.0002),
    ('Muzaffarpur', 'Muzaffarpur', 'Bihar', '842001', 26.1209, 85.3647),
    ('Ranchi', 'Ranchi', 'Jharkhand', '834001', 23.3441, 85.3096),
    ('Jamshedpur', 'East Singhbhum', 'Jharkhand', '831001', 22.8046, 86.2029),
    ('Dhanbad', 'Dhanbad', 'Jharkhand', '826001', 23.7957, 86.4304),
    ('Bhubaneswar', 'Khordha', 'Odisha', '751001', 20.2961, 85.8245),
    ('Cuttack', 'Cuttack', 'Odisha', '753001', 20.4625, 85.883),
    ('Guwahati', 'Kamrup Metropolitan', 'Assam', '781001', 26.1445, 91.7362),
    ('Dibrugarh', 'Dibrugarh', 'Assam', '786001', 27.4728, 94.912),
    ('Agartala', 'West Tripura', 'Tripura', '799001', 23.8315, 91.2868),
    ('Shillong', 'East Khasi Hills', 'Meghalaya', '793001', 25.5788, 91.8933),
    ('Imphal', 'Imphal West', 'Manipur', '795001', 24.817, 93.9368),
]


def seed():
    existing = {(p.pincode, p.office_name) for p in Pincode.query.all()}
    added = 0
    for name, district, state, pincode, lat, lng in LOCATIONS:
        if (pincode, name) in existing:
            continue
        db.session.add(Pincode(
            pincode=pincode, office_name=name, district=district,
            state=state, latitude=lat, longitude=lng, is_major=True,
        ))
        added += 1
    db.session.commit()
    print(f"locations: {added} added, {len(existing)} already present")


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        seed()
