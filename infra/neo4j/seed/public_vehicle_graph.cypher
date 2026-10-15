// LLMcopy 公开车型图谱测试数据：创建品牌、车型、版本、能源类型和配件兼容关系。
// 本脚本仅使用 MERGE 和 IF NOT EXISTS，可重复执行；严禁加入 MATCH (n) DETACH DELETE n 等清库语句。

CREATE CONSTRAINT unique_car_version IF NOT EXISTS
FOR (v:CarVersion) REQUIRE v.infoId IS UNIQUE;

CREATE CONSTRAINT unique_part_number IF NOT EXISTS
FOR (p:Part) REQUIRE p.partNo IS UNIQUE;

CREATE INDEX idx_brand_name IF NOT EXISTS
FOR (b:Brand) ON (b.name);

CREATE INDEX idx_car_model_name IF NOT EXISTS
FOR (m:CarModel) ON (m.name);

UNWIND [
  {name: 'Tesla', englishName: 'Tesla', country: '美国', founded: 2003, aliases: ['特斯拉']},
  {name: '比亚迪', englishName: 'BYD', country: '中国', founded: 1995, aliases: ['BYD', 'Build Your Dreams']},
  {name: '宝马', englishName: 'BMW', country: '德国', founded: 1916, aliases: ['BMW', 'Bayerische Motoren Werke']},
  {name: '蔚来', englishName: 'NIO', country: '中国', founded: 2014, aliases: ['NIO']},
  {name: '小米汽车', englishName: 'Xiaomi Auto', country: '中国', founded: 2021, aliases: ['Xiaomi', 'Xiaomi SU7']}
] AS item
MERGE (b:Brand {name: item.name})
SET b.englishName = item.englishName, b.country = item.country, b.founded = item.founded, b.aliases = item.aliases;

UNWIND [
  {name: 'Model 3', vehicleType: '轿车', fuelType: '电动', aliases: ['Tesla Model 3', '特斯拉 Model 3']},
  {name: 'Model Y', vehicleType: 'SUV', fuelType: '电动', aliases: ['Tesla Model Y', '特斯拉 Model Y']},
  {name: '汉', vehicleType: '轿车', fuelType: '电动', aliases: ['BYD Han', '比亚迪汉']},
  {name: '唐', vehicleType: 'SUV', fuelType: '混动', aliases: ['BYD Tang', '比亚迪唐']},
  {name: '3系', vehicleType: '轿车', fuelType: '汽油', aliases: ['BMW 3 Series', '宝马3系', 'BMW 3系']},
  {name: 'ET5', vehicleType: '轿车', fuelType: '电动', aliases: ['NIO ET5', '蔚来ET5']},
  {name: 'SU7', vehicleType: '轿车', fuelType: '电动', aliases: ['Xiaomi SU7', '小米SU7']}
] AS item
MERGE (m:CarModel {name: item.name})
SET m.vehicleType = item.vehicleType, m.fuelType = item.fuelType, m.aliases = item.aliases;

UNWIND [
  {name: '纯电动', aliases: ['BEV', 'Battery Electric Vehicle']},
  {name: '插电混动', aliases: ['PHEV', 'Plug-in Hybrid Electric Vehicle']},
  {name: '燃油', aliases: ['汽油', '柴油', 'Internal Combustion Engine']}
] AS item
MERGE (e:EnergyType {name: item.name}) SET e.aliases = item.aliases;

UNWIND [
  {infoId: 'CI_001', name: 'Model 3 2025焕新版', model: 'Model 3', energy: '纯电动', modelYear: 2025, version: '长续航全轮驱动版', seatCount: 5, motorPower: 331, rangeKm: 713, guidePrice: 27.19, batteryType: '磷酸铁锂'},
  {infoId: 'CI_002', name: 'Model 3 2025 Performance', model: 'Model 3', energy: '纯电动', modelYear: 2025, version: '高性能版', seatCount: 5, motorPower: 377, rangeKm: 623, guidePrice: 33.59, batteryType: '三元锂'},
  {infoId: 'CI_003', name: 'Model Y 2025长续航版', model: 'Model Y', energy: '纯电动', modelYear: 2025, version: '长续航全轮驱动版', seatCount: 5, motorPower: 331, rangeKm: 688, guidePrice: 29.09, batteryType: '三元锂'},
  {infoId: 'CI_004', name: '比亚迪汉 EV 2025', model: '汉', energy: '纯电动', modelYear: 2025, version: '715KM前驱旗舰型', seatCount: 5, motorPower: 180, rangeKm: 715, guidePrice: 21.98, batteryType: '磷酸铁锂'},
  {infoId: 'CI_005', name: '比亚迪唐 DM-i 2025', model: '唐', energy: '插电混动', modelYear: 2025, version: '200KM旗舰型', seatCount: 7, motorPower: 160, rangeKm: 200, guidePrice: 22.98, batteryType: '磷酸铁锂'},
  {infoId: 'CI_006', name: '宝马 3系 330i M运动曜夜套装', model: '3系', energy: '燃油', modelYear: 2025, version: '330i M运动曜夜套装', seatCount: 5, motorPower: 180, guidePrice: 38.19, batteryType: null},
  {infoId: 'CI_007', name: '蔚来 ET5 2025', model: 'ET5', energy: '纯电动', modelYear: 2025, version: '75kWh', seatCount: 5, motorPower: 360, rangeKm: 560, guidePrice: 29.80, batteryType: '三元锂'},
  {infoId: 'CI_008', name: '小米 SU7 Max', model: 'SU7', energy: '纯电动', modelYear: 2024, version: 'Max', seatCount: 5, motorPower: 495, rangeKm: 800, guidePrice: 29.99, batteryType: '三元锂'}
] AS item
MERGE (v:CarVersion {infoId: item.infoId})
SET v.name = item.name, v.modelYear = item.modelYear, v.version = item.version, v.seatCount = item.seatCount,
    v.motorPower = item.motorPower, v.rangeKm = item.rangeKm, v.guidePrice = item.guidePrice,
    v.batteryType = item.batteryType, v.dataStatus = 'TEST'
WITH item, v
MATCH (m:CarModel {name: item.model})
MATCH (e:EnergyType {name: item.energy})
MERGE (m)-[:HAS_VERSION]->(v)
MERGE (v)-[:HAS_ENERGY_TYPE]->(e);

UNWIND [
  {brand: 'Tesla', model: 'Model 3'}, {brand: 'Tesla', model: 'Model Y'},
  {brand: '比亚迪', model: '汉'}, {brand: '比亚迪', model: '唐'},
  {brand: '宝马', model: '3系'}, {brand: '蔚来', model: 'ET5'}, {brand: '小米汽车', model: 'SU7'}
] AS item
MATCH (b:Brand {name: item.brand})
MATCH (m:CarModel {name: item.model})
MERGE (b)-[:HAS_MODEL]->(m);

UNWIND [
  {partNo: 'P_001', name: '米其林轮胎 245/45 R19', category: '轮胎', brand: 'Michelin'},
  {partNo: 'P_002', name: '博世刹车片', category: '制动系统', brand: 'Bosch'},
  {partNo: 'P_003', name: 'NOMI 车载智能伙伴', category: '智能座舱', brand: 'NIO'},
  {partNo: 'P_004', name: '宁德时代三元锂电池 75kWh', category: '动力电池', brand: 'CATL'},
  {partNo: 'P_005', name: '比亚迪刀片电池 85kWh', category: '动力电池', brand: 'BYD'},
  {partNo: 'P_006', name: 'Brembo 四活塞刹车卡钳', category: '制动系统', brand: 'Brembo'},
  {partNo: 'P_007', name: '空气悬架系统', category: '底盘系统', brand: 'Continental'},
  {partNo: 'P_008', name: '哈曼卡顿音响系统', category: '影音娱乐', brand: 'Harman Kardon'},
  {partNo: 'P_009', name: '毫米波雷达模块', category: '智能驾驶', brand: 'Bosch'},
  {partNo: 'P_010', name: '激光雷达模块', category: '智能驾驶', brand: 'Hesai'}
] AS item
MERGE (p:Part {partNo: item.partNo}) SET p.name = item.name, p.category = item.category, p.brand = item.brand, p.dataStatus = 'TEST';

UNWIND [
  {version: 'CI_001', part: 'P_001'}, {version: 'CI_006', part: 'P_001'},
  {version: 'CI_004', part: 'P_002'}, {version: 'CI_006', part: 'P_002'},
  {version: 'CI_007', part: 'P_003'},
  {version: 'CI_001', part: 'P_004'}, {version: 'CI_002', part: 'P_004'}, {version: 'CI_003', part: 'P_004'}, {version: 'CI_007', part: 'P_004'},
  {version: 'CI_004', part: 'P_005'}, {version: 'CI_005', part: 'P_005'},
  {version: 'CI_002', part: 'P_006'}, {version: 'CI_008', part: 'P_006'},
  {version: 'CI_007', part: 'P_007'}, {version: 'CI_008', part: 'P_007'},
  {version: 'CI_006', part: 'P_008'},
  {version: 'CI_001', part: 'P_009'}, {version: 'CI_003', part: 'P_009'}, {version: 'CI_008', part: 'P_009'},
  {version: 'CI_008', part: 'P_010'}
] AS item
MATCH (v:CarVersion {infoId: item.version})
MATCH (p:Part {partNo: item.part})
MERGE (v)-[:USES_PART]->(p);
