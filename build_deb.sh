#!/bin/bash

dpkg-buildpackage -us -uc -j4 

./debian/rules clean
echo "Derectory cleaned"

echo "ALL OK"