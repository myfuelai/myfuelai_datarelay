import os

import xml.etree.ElementTree as ET

def xml_to_dict(element):
    """Recursively convert XML to dictionary"""
    result = {}
    for child in element:
        tag = child.tag.split('}')[-1]  # remove namespace
        value = xml_to_dict(child) if list(child) else (child.text or "").strip()

        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result


# ---- SOAP XML ----
with open(os.path.join(os.path.dirname(__file__), "GetDataResponse.txt"), "r", encoding="utf-8") as f:
    soap_xml = f.read()
# soap_xml = """<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">

#    <soap:Body>
#       <GetMasterDataResponse xmlns="http://profdata.com.Petronet">
#          <GetMasterDataResult><![CDATA[
#          <PDIMasterFile>
#             <Origins>
#                <DispatchAreas />
#                <Terminals />
#                <CompanyOwnedBulkPlants />
#             </Origins>
#             <SuppliersAndCarriers>
#                <Vehicles />
#                <Drivers />
#             </SuppliersAndCarriers>
#             <Destinations>
#                <Sites />
#                <Customers />
#             </Destinations>
#             <TankCharts />
#             <ProductAuthorizationZones />
#             <Products>
#                <FuelProducts />
#                <WarehouseProducts />
#                <NonInventoryProducts />
#             </Products>
#          </PDIMasterFile>
#          ]]></GetMasterDataResult>
#       </GetMasterDataResponse>
#    </soap:Body>
# </soap:Envelope>"""

# ---- Parse SOAP ----
root = ET.fromstring(soap_xml)

# Find CDATA XML text
namespaces = {
    'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
    'ns': 'http://profdata.com.Petronet'
}

cdata_xml = root.find('.//ns:GetMasterDataResult', namespaces).text.strip()

# ---- Parse inner XML (from CDATA) ----
inner_root = ET.fromstring(cdata_xml)

# ---- Convert to Python dict ----
result_dict = {inner_root.tag: xml_to_dict(inner_root)}

print(result_dict)
