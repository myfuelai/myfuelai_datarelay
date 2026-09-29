"""PDI Enterprise SOAP envelopes - moved unchanged from the old fastapi_listener app.py."""


def build_fuel_orders_payload(operation: str, password: str, partner_id: str, **kwargs) -> str:
    statuses = kwargs.get("StatusToInclude", [])
    if isinstance(statuses, (list, tuple)):
        statuses_list = [str(s) for s in statuses]
    else:
        statuses_list = [s.strip() for s in str(statuses).split(",")] if statuses else []

    statuses_xml = "\n                                ".join(
        f"<Status>{s}</Status>" for s in statuses_list
    ) if statuses_list else ""

    records = kwargs.get("RecordsToInclude", "")

    return f"""
    <s:Envelope
	xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
	<s:Header>
		<UserCredentials
			xmlns:h="http://profdata.com.Petronet"
			xmlns="http://profdata.com.Petronet"
			xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
			xmlns:xsd="http://www.w3.org/2001/XMLSchema">
			<Password>{password}</Password>
            <PartnerID>{partner_id}</PartnerID>
		</UserCredentials>
	</s:Header>
	<s:Body
		xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
		xmlns:xsd="http://www.w3.org/2001/XMLSchema">
		<GetFuelOrders
			xmlns="http://profdata.com.Petronet">
			<PDIGetFuelOrdersFilter>
				<PDIGetFuelOrdersInput
					xmlns="">
					<StatusToInclude>
                        {statuses_xml}
					</StatusToInclude>
					<RecordsToInclude>{records}</RecordsToInclude>
				</PDIGetFuelOrdersInput>
			</PDIGetFuelOrdersFilter>
		</GetFuelOrders>
	</s:Body>
</s:Envelope>
    """


def get_master_data_body(operation: str, password: str, partner_id: str, **kwargs) -> str:
    return f"""
    <s:Envelope
        xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
        <s:Header>
            <UserCredentials
                xmlns:h="http://profdata.com.Petronet"
                xmlns="http://profdata.com.Petronet"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xmlns:xsd="http://www.w3.org/2001/XMLSchema">
                <Password>{password}</Password>
                <PartnerID>{partner_id}</PartnerID>
            </UserCredentials>
        </s:Header>
        <s:Body
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:xsd="http://www.w3.org/2001/XMLSchema">
            <GetMasterData
                xmlns="http://profdata.com.Petronet">
                <mode>{kwargs['mode']}</mode>
            </GetMasterData>
        </s:Body>
    </s:Envelope>
    """


def build_soap_payload(operation: str, password: str, partner_id: str, **kwargs) -> str:
    if operation == "GetFuelOrders":
        return build_fuel_orders_payload(operation, password, partner_id, **kwargs)
    if operation == "GetMasterData":
        return get_master_data_body(operation, password, partner_id, **kwargs)
    # GetFuelLoads was a placeholder (its filter body was never written) and its task was disabled.
    return None
