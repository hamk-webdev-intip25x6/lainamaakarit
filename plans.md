Implement the project in PR phases, one at a time. Don't proceed unless all the tests pass.

# PR1 - Skeleton web site

1. Base template, witch is responsive and mobile friendly. Base template also implements light/dark/high dark/high light contrast mode, saving user settings in local storage. Use vanilla JS. Don't over rely on any 3rd party JS libraries and CSS frameworks. You can use Bootstrap. For the data base use 4 tables.
Kirja that is kirja_id: nimi, tekija, tehty, avainsana
Komentti that is komentti_id: kayttaja_id, kirja_id, teksti, arvosana
Laina that is laina_id: kirja_id, kauttaja_id, laina_alku, laina:loppu
Under django authentication user table:
kauttaja that is kauttaja_id: nimi, salasana
2. Login - using Django standard login.
3. Logout - using Django standard logout
4. Signup - Create new user accounts with own form. Username and password. Encrypt the password.
5. Home page, witch is shows the user either logged in and has a logout button or link to log in or signup.

# PR2 PDF library and loaning

1. Create a library folder where pdf files are stored. Create fields based on the identified sample books.

2. Keep the original PDFs encrypted. When user loans a book it creates a temporary unencrypted copy and link to it. Then shares it with user for 3 days before deleting it, or until user ends the loan. For automatic testing use seconds and for manual testing few minutes.

3. Create a front end for searching a browsing available PDF books. and the ability to loan them.


# PR3 - Library reviews and ratings

1. reviews for the book.
- review, witch includes review text (TextField), star grade (integer, 1-5) automatic creation date and time.
- User can only review a book once, but can modify it after.
- User star system for the reviews from 1 to 5 stars.
- Make sure the style has tight, but reasonable constraints so it doesn't break the page.

2. Rating system
- Take the average on the stars and show it in the book loan page.
- Do this by calculating median (average) of grades for a book.

# PR4 Admin panel

1. Admin panel for maintenance
- Huolto is the name for the maintenance where admin users can modify book loans or end them early. They can also delist books and delete reviews.
- feed for new comments.
- include filter for creation date for the comments


# PR5 - Populate DB
- populate the system, add 5 invented Finnish users, 1-2 different book loans. Also least 3 reviews for each book. Don't add anything to DB if that data is already there
- Generate a script to run a local web server on the current folder, use curl and port 8002 by default, user can change the port. Allow viewing of all suborders by default.

# PR6 - Testing and documentation
- add unit tests and tests the forms as well and any other test that would be useful.
- install playwright NOTE: use .venv and playwright. Take long screenshots of the different pages. Check the screenshots for readability and theming problems on different screen sizes.
- update README.md for good user documentation.
- Embed screenshots to the README.md