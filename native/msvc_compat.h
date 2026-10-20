#pragma once
/* Upstream scalar C uses the POSIX strcasecmp spelling. No codec changes. */
#ifdef _MSC_VER
#include <string.h>
#define strcasecmp _stricmp
#endif
