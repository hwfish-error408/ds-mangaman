#---------------------------------------------------------------------------------
.SUFFIXES:
#---------------------------------------------------------------------------------
ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment.")
endif

# Include the official devkitARM build chain rules
include $(DEVKITARM)/ds_rules

#---------------------------------------------------------------------------------
# Core project directory configuration
#---------------------------------------------------------------------------------
TARGET      :=  ds_comic_viewer
BUILD       :=  build
SOURCES     :=  source
INCLUDES    :=  include

#---------------------------------------------------------------------------------
# Architecture and compilation parameters (completely matching the official standard)
#---------------------------------------------------------------------------------
ARCH    :=  -march=armv5te -mtune=arm946e-s -mthumb-interwork -mthumb

CFLAGS  :=  -g -Wall -O2 -ffunction-sections -fdata-sections $(ARCH) -DARM9 -D__NDS__
CFLAGS  +=  $(INCLUDE) -I$(DEVKITPRO)/calico/include
CXXFLAGS:=  $(CFLAGS) -fno-rtti -fno-exceptions

LDFLAGS =   -specs=ds_arm9.specs -g $(ARCH) -Wl,-Map,$(notdir $*.map) -L$(DEVKITPRO)/calico/lib

# Core dependent libraries
LIBS    := -lfat -lnds9 -lcalico_ds9
LIBDIRS :=  $(DEVKITPRO)/libnds

#---------------------------------------------------------------------------------
# Two-stage recursive core control flow
#---------------------------------------------------------------------------------
ifneq ($(BUILD),$(notdir $(CURDIR)))

export OUTPUT   :=  $(CURDIR)/$(TARGET)
export VPATH    :=  $(foreach dir,$(SOURCES),$(CURDIR)/$(dir))
export DEPSDIR  :=  $(CURDIR)/$(BUILD)

CFILES      :=  $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
CPPFILES    :=  $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))

export LD   :=  $(CXX)
export OFILES   :=  $(CPPFILES:.cpp=.o) $(CFILES:.c=.o)

export INCLUDE  :=  $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
                    $(foreach dir,$(LIBDIRS),-I$(dir)/include) \
                    -I$(CURDIR)/$(BUILD)

export LIBPATHS :=  $(foreach dir,$(LIBDIRS),-L$(dir)/lib)

.PHONY: $(BUILD) clean

$(BUILD):
	@[ -d $@ ] || mkdir -p $@
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile NITRO_FILES=$(CURDIR)/nitrofiles

clean:
	@echo clean ...
	@rm -fr $(BUILD) $(TARGET).elf $(TARGET).nds $(TARGET).map

#---------------------------------------------------------------------------------
else

DEPENDS :=  $(OFILES:.o=.d)

# Restore official native implicit build rules, handled by official scripts to match the new ARM7 firmware
$(OUTPUT).nds   :   $(OUTPUT).elf
$(OUTPUT).elf   :   $(OFILES)

-include $(DEPENDS)

endif
#---------------------------------------------------------------------------------