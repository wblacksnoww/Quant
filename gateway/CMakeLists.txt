cmake_minimum_required(VERSION 3.16)
project(quant-gateway CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)
find_package(Boost 1.74 REQUIRED)  # Beast header-only；asio 自 1.69 起不强制 libboost_system

set(GW_INCLUDES
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_CURRENT_SOURCE_DIR}/third_party
  ${CMAKE_CURRENT_SOURCE_DIR}/../backend/cpp)

add_executable(quant-gateway
  src/main.cpp
  src/ws_client.cpp
  src/tick_engine.cpp
  src/risk.cpp
  src/audit.cpp
  src/ipc.cpp
  src/rest_okx.cpp
  ../backend/cpp/core.cpp      # qcore 账本/风控实现
  ../backend/cpp/indicators.cpp
  ../backend/cpp/strategy.cpp  # spec 编译与求值（拼装策略）
)
target_include_directories(quant-gateway PRIVATE ${GW_INCLUDES})
target_link_libraries(quant-gateway PRIVATE OpenSSL::SSL OpenSSL::Crypto Threads::Threads Boost::headers)

enable_testing()
add_executable(gateway-tests
  tests/test_main.cpp
  tests/test_strategies.cpp
  tests/test_tick_engine.cpp
  src/tick_engine.cpp
  src/risk.cpp
  src/audit.cpp
  ../backend/cpp/core.cpp
  ../backend/cpp/indicators.cpp
  ../backend/cpp/strategy.cpp
)
target_include_directories(gateway-tests PRIVATE ${GW_INCLUDES})
add_test(NAME gateway-tests COMMAND gateway-tests)
