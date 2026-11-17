# Customer and staff workflow

Both portals use the same bookings, tenant links, rentals, meter readings,
invoices and payments. Customer actions create requests; staff approval changes
the shared records. Refresh a page to see the latest status.

| Stage | Customer portal | Staff workspace |
| --- | --- | --- |
| Find a room | Browse available rooms and request a booking | Dashboard shows a pending booking |
| Confirm booking | See confirmation and agreement preparation status | Customer requests → confirm availability; the room is reserved |
| Prepare rental | See that the agreement is being prepared | Prepare rentals → Prepare rental opens the selected booking with move-in date and listed rent filled in; review dates, rent, deposit and due day |
| Activate rental | See the current home, or an upcoming rental before its start date | Open the rental detail and activate it; a primary customer is required |
| Submit utilities | Upload a meter value and photo; see Awaiting review | Review the photo, verify the starting value and saved rate, correct the draft if necessary, then approve |
| Issue monthly bill | Rent and approved utilities appear in My Rental | Invoices & utilities → select the rental and month; every active meter needs an approved reading |
| Submit payment | Scan the configured bank QR, transfer money, then submit reference and proof; see Payment in review | Customer requests → payment proofs; the invoice also links to pending proof review |
| Confirm receipt | See the updated paid amount and remaining balance | Verify receipt in the bank, then confirm the proof; partial payments leave the remaining balance due |

Confirmed bookings stay in Prepare rentals until activation. An existing draft
is opened instead of offering another agreement. Activating a rental converts
its booking. Customers cannot cancel a confirmed booking through the pending
request cancellation button; contact management for changes.

All invoice entry points require approved readings from every active room meter.
An issued month cannot accept new readings through customer or generic staff
forms. Use the existing draft edit screen to correct readings before approval.
Both approval screens recalculate the charge using the saved values and rate.
Approved and billed readings remain protected.

Pending payment proofs do not reduce a balance. Customers wait for review before
submitting another proof for the same invoice. Staff should review a pending
proof before manually recording the same transfer. Confirmation applies the
payment once. Rejected proofs leave the balance unchanged; the customer should
check with management before submitting corrected evidence.

There is no banking callback, email/SMS notification, or automatic page polling.
Move-out, renewal, deposit settlement and payment reversal still require further
workflow development. This change does not add those lifecycle operations.
