from fastapi import FastAPI, Response
import uvicorn
from pydantic import BaseModel
import requests
from fastapi import HTTPException

app = FastAPI()

@app.get("/data.xml")
async def get_xml():
    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<SAPEvent>
    <Header>
        <System>SAP-ECC</System>
        <EventType>ORDER_CREATED</EventType>
        <Timestamp>2026-01-10T10:30:00</Timestamp>
    </Header>
    <Order>
        <OrderID>ORD12345</OrderID>
        <Customer>
            <Name>ABC Industries</Name>
            <Code>CUST001</Code>
        </Customer>
        <Amount currency="INR">150000</Amount>
        <Items>
            <Item>
                <Material>Steel Rod</Material>
                <Quantity>10</Quantity>
            </Item>
            <Item>
                <Material>Iron Sheet</Material>
                <Quantity>5</Quantity>
            </Item>
        </Items>
    </Order>
</SAPEvent>
"""
    return Response(content=xml_content, media_type="application/xml")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
    