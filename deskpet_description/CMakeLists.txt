# Renamed and modified from ros_gz_example_description in
# ros_gz_project_template (Apache 2.0) for the deskpet project.
cmake_minimum_required(VERSION 3.5)

project(deskpet_description)

find_package(ament_cmake REQUIRED)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

install(
  DIRECTORY
    models/
  DESTINATION share/${PROJECT_NAME}/models
)

ament_environment_hooks("${CMAKE_CURRENT_SOURCE_DIR}/hooks/${PROJECT_NAME}.dsv.in")
ament_environment_hooks("${CMAKE_CURRENT_SOURCE_DIR}/hooks/${PROJECT_NAME}.sh.in")

ament_package()
