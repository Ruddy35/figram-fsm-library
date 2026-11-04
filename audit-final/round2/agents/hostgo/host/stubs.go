package main

import "hostgo/refwrap"

func setCorruption(name string) { refwrap.SetCorrupt(refwrap.Corruption(name)) }

