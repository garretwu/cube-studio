"""
Shared Redfish mock payloads for channel tests.
"""

TEST_BMC_HOST = "10.11.8.13"
TEST_USERNAME = "admin"
TEST_PASSWORD = "Admin@9000"
TEST_TOKEN = "mock-token"
TEST_SESSION_LOCATION = "/redfish/v1/SessionService/Sessions/abcd1234"

SERVICE_ROOT = {
    "RedfishVersion": "1.18.0",
    "UUID": "d2e4f3c2-7ea6-4f5b-a8cc-b1700fd41234",
    "Vendor": "AMI",
    "Product": "BMC",
    "Managers": {"@odata.id": "/redfish/v1/Managers"},
    "Systems": {"@odata.id": "/redfish/v1/Systems"},
    "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
    "UpdateService": {"@odata.id": "/redfish/v1/UpdateService"},
}

MANAGERS_COLLECTION = {"Members": [{"@odata.id": "/redfish/v1/Managers/Self"}]}
SYSTEMS_COLLECTION = {"Members": [{"@odata.id": "/redfish/v1/Systems/Self"}]}
CHASSIS_COLLECTION = {"Members": [{"@odata.id": "/redfish/v1/Chassis/Self"}]}

MANAGER_SELF = {
    "Id": "Self",
    "Name": "Manager",
    "FirmwareVersion": "2.13.0",
    "Actions": {"#Manager.Reset": {}, "#Manager.ResetToDefaults": {}},
}

SYSTEM_SELF = {
    "Id": "Self",
    "Name": "System",
    "Model": "Server",
    "Manufacturer": "Vendor",
    "PowerState": "On",
    "Actions": {
        "#ComputerSystem.Reset": {
            "target": "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
            "ResetType@Redfish.AllowableValues": ["On", "ForceRestart", "GracefulRestart"],
        }
    },
}

CHASSIS_SELF = {
    "Id": "Self",
    "Name": "Computer System Chassis",
    "Actions": {"#Chassis.Reset": {}},
}

UPDATE_SERVICE = {
    "Actions": {
        "#UpdateService.SimpleUpdate": {"target": "/redfish/v1/UpdateService/Actions/UpdateService.SimpleUpdate"}
    }
}

THERMAL_PAYLOAD = {
    "Fans": [{"Name": "Fan1", "Reading": 9200}],
    "Temperatures": [{"Name": "CPU Temp", "ReadingCelsius": 49}],
}
