from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Gateway(BaseModel):
    host: str
    ssh_port: int = 22


class Router(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,63}$")
    display_name: str
    gateway: str
    reverse_port: int = Field(ge=1, le=65535)
    model: str
    egress_policy: str = "foreign"


class Inventory(BaseModel):
    central: dict[str, str]
    gateways: dict[str, Gateway]
    routers: list[Router]

    def gateway_for(self, router: Router) -> Gateway:
        if router.gateway == "central":
            return Gateway(host=self.central.get("host", "central-monitor.example"))
        return self.gateways[router.gateway]


def load_inventory(path: Path) -> Inventory:
    with path.open(encoding="utf-8") as stream:
        return Inventory.model_validate(yaml.safe_load(stream))
