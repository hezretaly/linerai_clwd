# Al's Bou Cars — their lot goes here

Drop their export in as `inventory.csv`, then point the profile at it:

```yaml
# backend/config/dealerships/alsbou.yaml
inventory:
  fixture_csv: "alsbou/inventory.csv"
```

`make reset-db DEALERSHIP=alsbou` then rebuilds their real lot with **no
network at all**, which is the answer when a dealer's site refuses the crawler
— as this one does from here: the egress proxy answers 403 to CONNECT for
`www.alsboucars.com:443`, so nothing on this machine has read their site.

It goes through the same CSV importer a dealer's own upload does
(`/app/inventory/import`), so nothing here is a private path — if the file
imports through the screen, it seeds.

## Columns

```
vin,year,make,model,trim,price,mileage,body_style,seats,features,
photo_url,listing_url,location,dealer_phone,doc_fee,stock_number
```

`vin,year,make,model,trim,price,mileage,body_style,seats` is what the importer
requires. The rest are optional, and common DMS aliases are mapped to these
names — a real export does not use them.

Three things worth knowing before you shape the file:

- **Cost columns are dropped before a row is built.** Acquisition cost, margin
  and salesperson fields never enter the database. Once a column exists it
  reaches `search_inventory`, and from there the model; a prompt telling the
  model to ignore a field it can see is a request, not a guarantee.
- **An empty `price` is a listing state, not a gap.** Those cars sort *last*
  rather than first (SQLite orders NULL before every number, which once put
  every call-for-price car at the top of "what's cheapest?"), and the buyer
  gets an enquiry link on the card instead of a number in a sentence.
- **`location` is per car.** If they run more than one lot, every car says
  which one it is on, and Liner tells a buyer before offering them a time —
  the appointment is at the one address in the profile.

`body_style` and `seats` may be left empty. A Dealer Car Search crawl leaves
them blank too, so the two `search_inventory` filters that read them simply
narrow nothing; a missing field is a smaller error than an invented one, and
the keyword search still matches on the rest of the row.
