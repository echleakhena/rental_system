"""Room edit saves details and photos as one operation."""

import unittest
from io import BytesIO
from pathlib import Path

import test_customer as fixtures

from rpms.models import MODELS as M
from rpms.models import db


class RoomEditPhotoTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    csrf = fixtures.CustomerTests.csrf

    def save(self, **changes):
        data = dict(
            _csrf="test",
            floor_id=self.room.floor_id,
            room_number=self.room.room_number,
            monthly_rent="175",
            status="Vacant",
        )
        data.update(changes)
        return self.staff.post(
            f"/records/rooms/{self.room.id}/edit",
            data=data,
            content_type="multipart/form-data",
        )

    def test_edit_upload_replace_remove_and_customer_visibility(self):
        path = f"/records/rooms/{self.room.id}/edit"
        page = self.staff.get(path).get_data(as_text=True)
        self.assertIn('enctype="multipart/form-data"', page)
        for slot in range(4):
            self.assertIn(f'name="photo_{slot}"', page)
        self.assertEqual(
            self.save(photo_0=fixtures.photo(), photo_1=fixtures.photo()).status_code,
            302,
        )
        db.session.refresh(self.room)
        original = list(self.room.photos)
        folder = Path(self.temp.name) / "room_photos"
        self.assertTrue((folder / original[0]).exists())
        self.assertEqual(self.room.monthly_rent, 175)
        with self.client.get(f"/customer/rooms/{self.room.id}/photo/0") as response:
            self.assertEqual(response.status_code, 200)
        self.assertIn('name="remove_0"', self.staff.get(path).get_data(as_text=True))
        self.save(photo_0=fixtures.photo(), remove_1="yes", monthly_rent="180")
        db.session.refresh(self.room)
        self.assertNotEqual(self.room.photos[0], original[0])
        self.assertIsNone(self.room.photos[1])
        self.assertFalse((folder / original[0]).exists())
        self.assertFalse((folder / original[1]).exists())
        self.assertTrue((folder / self.room.photos[0]).exists())
        self.assertEqual(self.room.monthly_rent, 180)
        with self.client.get(f"/customer/rooms/{self.room.id}/photo/1") as response:
            self.assertEqual(response.status_code, 404)
        kept = list(self.room.photos)
        self.save()
        db.session.refresh(self.room)
        self.assertEqual(self.room.photos, kept)

    def test_invalid_photo_preserves_details_and_existing_files(self):
        self.save(photo_0=fixtures.photo())
        db.session.refresh(self.room)
        original = list(self.room.photos)
        response = self.save(
            monthly_rent="999", remove_0="yes", photo_1=(BytesIO(b"invalid"), "bad.png")
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(self.room)
        self.assertEqual(self.room.monthly_rent, 175)
        self.assertEqual(self.room.photos, original)
        self.assertTrue((Path(self.temp.name) / "room_photos" / original[0]).exists())

    def test_database_failure_cleans_new_files_and_preserves_old_photo(self):
        self.save(photo_0=fixtures.photo())
        db.session.refresh(self.room)
        original = list(self.room.photos)
        other = M["rooms"](
            floor_id=self.room.floor_id, room_number="DUPLICATE", status="Vacant"
        )
        db.session.add(other)
        db.session.commit()
        response = self.save(room_number="DUPLICATE", photo_0=fixtures.photo())
        self.assertEqual(response.status_code, 200)
        db.session.refresh(self.room)
        self.assertEqual(self.room.photos, original)
        self.assertEqual(
            {p.name for p in (Path(self.temp.name) / "room_photos").iterdir()},
            {original[0]},
        )

    def test_viewer_cannot_update_photos(self):
        viewer_role = M["roles"](name="Viewer", is_active=True)
        db.session.add(viewer_role)
        db.session.flush()
        viewer = M["users"](
            role_id=viewer_role.id,
            name="Viewer",
            email="viewer-photo@example.com",
            is_active=True,
        )
        db.session.add(viewer)
        db.session.commit()
        with self.staff.session_transaction() as session:
            session.update(uid=viewer.id, csrf="test")
        self.assertEqual(self.save(photo_0=fixtures.photo()).status_code, 403)
        db.session.refresh(self.room)
        self.assertFalse(self.room.photos)
