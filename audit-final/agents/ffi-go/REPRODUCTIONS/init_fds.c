#include <stdio.h>
#include <dirent.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdlib.h>
int figram_init(void);
static void fds(const char* w){ DIR* d=opendir("/proc/self/fd"); struct dirent* e; printf("%s:", w);
  while((e=readdir(d))) if(e->d_name[0]!='.'){ char p[64],t[256]; snprintf(p,64,"/proc/self/fd/%s",e->d_name); ssize_t n=readlink(p,t,255); t[n>0?n:0]=0; int fl=fcntl(atoi(e->d_name),F_GETFD); printf(" %s->%s%s", e->d_name, t, (fl&FD_CLOEXEC)?"(cloexec)":""); } printf("\n"); closedir(d);}
int main(){ fds("before"); figram_init(); fds("after"); return 0; }
