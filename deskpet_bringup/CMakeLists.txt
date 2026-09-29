# Renamed and modified from ros_gz_example_bringup in ros_gz_project_template
# (Apache 2.0) for the deskpet project.
cmake_minimum_required(VERSION 3.5)

project(deskpet_bringup)

find_package(ament_cmake REQUIRED)
find_package(deskpet_description REQUIRED)

# Install project launch files
install(
  DIRECTORY
    launch/
  DESTINATION share/${PROJECT_NAME}/launch
)

# Install project configuration files
install(
  DIRECTORY
    config/
  DESTINATION share/${PROJECT_NAME}/config
)

# Install world files
install(
  DIRECTORY
    worlds/
  DESTINATION share/${PROJECT_NAME}/worlds
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
