cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(sim_hub)

target_include_directories(app PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../common src)
target_sources(app PRIVATE
	src/main.c src/radio.c src/usb.c src/store.c src/usb_init.c
	../common/axis_proc.c)
