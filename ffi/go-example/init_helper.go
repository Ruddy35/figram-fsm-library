package main

/*
int figram_init(void);
*/
import "C"

func initFigram() int { return int(C.figram_init()) }
