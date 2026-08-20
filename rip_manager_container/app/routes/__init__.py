"""HTTP routers, grouped by what they control."""

from routes import auth as auth_routes
from routes import adoption as adoption_routes
from routes import control as control_routes
from routes import drive_mapping as drive_mapping_routes
from routes import fleet as fleet_routes
from routes import settings as settings_routes
from routes import updates as update_routes

ROUTERS = (
    auth_routes.router,
    adoption_routes.router,
    settings_routes.router,
    update_routes.router,
    fleet_routes.router,
    control_routes.router,
    drive_mapping_routes.router,
)
