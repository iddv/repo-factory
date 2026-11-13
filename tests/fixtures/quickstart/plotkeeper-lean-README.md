# Plotkeeper

Plotkeeper is a small self-hosted web app for the **volunteer coordinators of one community garden**. It replaces the spreadsheets for:

- every plot and who holds it,
- a strictly ordered waiting list, offering each freed plot to the next person in line,
- the yearly fee for each plot holder, payments with receipts, and who has paid this season and who still owes.

Coordinators sign in with their own email and password. Gardeners and people on the waiting list are records that coordinators keep; they do not sign in.

Everything runs on one machine with Node.js. It needs no other software, no database server and no internet connection.

## Quick start

You need **Node.js 22.13 or newer** (check with `node --version`; get it from <https://nodejs.org/>).

In the Plotkeeper folder, run:

```sh
sh install.sh
```

Open <http://127.0.0.1:4680/> in a browser on the same machine. Fill in the setup page (garden name, currency, your name, email and a password of at least 10 characters). You are signed in and see an empty dashboard.

To try it with sample data instead, open a second terminal in the same folder and run:

```sh
npm run demo
```

It prints a demo email and password once. Sign in with those. The demo only loads into an install with no plots, people or payments.

Stop Plotkeeper with Ctrl+C. Start it again later with:

```sh
npm start
```

## Install

`sh install.sh` (or `npm run setup`, which runs the same script) does everything on a fresh machine:

1. checks that Node.js 22.13 or newer is installed,
2. runs `npm install` (Plotkeeper has no dependencies beyond Node.js itself; this only records that),
3. creates the data folder and the database, and applies the database schema (`node bin/plotkeeper.js migrate`),
4. starts the app (`node bin/plotkeeper.js start`).

A fresh install is empty. There is no default account or password. The first person to open the address creates the first coordinator account, so do the setup straight after installing.

Plotkeeper is tested on Linux. It needs `sh` for the install script. Without `sh`, run the steps by hand: `npm install`, then `npm start`.
