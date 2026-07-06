# Thorlabs APT Stage Controller

Legacy Thorlabs APT / Kinesis yazılımının yerine geçen, modern bir PyQt5 arayüzü.
Aynı anda **3 adet Thorlabs TDC001 / KDC101 kontrolcü + MTS50/M-Z8 kızak** kontrol
eder. Tek kod tabanı hem **macOS** hem **Windows** üzerinde çalışır.

APT protokolünü doğrudan konuşur (`devices.py`); `thorlabs-apt-device` gibi bir
üst katmana bağımlı değildir.

## Özellikler

- 3 motoru yan yana, eşzamanlı kontrol (renk kodlu paneller)
- Canlı pozisyon ve durum göstergeleri (home / hareket / etkin)
- Mutlak konum, göreli hareket, jog ve home
- **"Ev = Sıfırla"**: mevcut konumu 0.0000 mm (kullanıcı referansı) yapar —
  yazılım ofsetiyle, donanım enkoderine dokunmadan (0–50 mm limitleri korunur);
  ↺ ile donanım koordinatlarına dönülür
- Ayarlanabilir hız ve ivme
- Otomatik cihaz keşfi + **cihaz bulunamadığında neden bulunamadığını açıklayan
  teşhis** (güç/kablo/sürücü kontrol listesi)
- Motorlara takma ad verme (kalıcı, `config.json`)

## Platforma göre bağlantı yöntemi

| Platform | Yöntem | Sürücü |
|----------|--------|--------|
| macOS    | libusb (pyftdi) | Ek sürücü gerekmez — Thorlabs özel PID'i (0xFAF0) Apple'ın FTDI sürücüsünce sahiplenilmez, doğrudan libusb ile erişilir. `brew install libusb` gerekir. |
| Windows  | FTDI **D2XX** (`ftd2xx`), COM yedeği | Thorlabs Kinesis/APT kurulumu FTDI **D2XX** sürücüsünü kurar; bu modda cihaz bir **COM portu olarak GÖRÜNMEZ**, D2XX ile doğrudan erişilir. (VCP sürücüsü kuruluysa COM portu da denenir.) `pip install ftd2xx` gerekir; Kinesis kuruluysa D2XX DLL zaten mevcuttur. |

Cihazlar `VID 0x0403 : PID 0xFAF0` ("APT DC Motor Controller", üretici "Thorlabs")
olarak görünür.

## Kurulum

### Ortak (Python 3.9+)

```bash
pip install -r requirements.txt
```

### macOS ek adımı

```bash
brew install libusb
```

### Windows
`INSTALL.bat` dosyasına çift tıklayın — Python yoksa indirir, bağımlılıkları
kurar ve isteğe bağlı olarak tek dosyalık `.exe` üretir.

## Çalıştırma

```bash
python main.py
```

Windows'ta `RUN.bat` (varsa derlenmiş `.exe`'yi, yoksa `python main.py`'yi çalıştırır).

Kullanım: **Cihazları Tara → Tümünü Bağla → Home / hareket**. Tarama 0 cihaz
bulursa açılan pencere nedenini söyler (çoğunlukla USB/güç kablosu).

## Windows'ta tek dosyalık .exe üretme

```batch
build_windows.bat
```

`dist\ThorlabsAPT.exe` oluşur; Python kurulu olmayan PC'lere kopyalanabilir.

## Proje yapısı

| Dosya | Görev |
|-------|-------|
| `main.py` | Uygulama girişi |
| `gui.py` | PyQt5 arayüzü |
| `devices.py` | Çapraz-platform APT protokol sürücüsü + cihaz keşfi + `diagnose()` |
| `config.json` | Motor takma adları |
| `requirements.txt` | Python bağımlılıkları |
| `INSTALL.bat` / `RUN.bat` / `build_windows.bat` | Windows yardımcı script'leri |

## Motor (MTS50/M-Z8) teknik değerleri

- Hareket aralığı: 0–50 mm
- Maksimum hız: 2.4 mm/s
- Maksimum ivme: 4.5 mm/s²
- Minimum adım: 0.0008 mm (0.8 µm)
- Sayaç çözünürlüğü: 34304 sayım/mm

## Cihaz bulunamıyorsa (sorun giderme)

1. **Güç:** T-Cube/K-Cube kutusu harici güce (güç kaynağı veya hub) bağlı mı?
   Sadece USB ile beslenmez; güç yoksa USB'de hiç görünmez.
2. **Kablo:** USB kablosu **veri** taşıyan bir kablo mu? Sadece-şarj kabloları
   cihazı besler ama veri hattı yoktur → görünmez.
3. **Bağlantı:** Farklı bir USB port/kablo, mümkünse doğrudan bilgisayara (hub'sız).
4. **Meşgul:** Eski Thorlabs APT/Kinesis yazılımı açıksa cihazı kilitler — kapatın.
5. macOS'ta `brew install libusb` kurulu olduğundan emin olun.
6. **Windows'ta cihaz bulunamıyorsa** (en sık neden): Thorlabs D2XX sürücüsü
   kullanılır, cihaz COM portu olarak görünmez. `pip install ftd2xx` yaptığınızdan
   ve Kinesis/APT yazılımının **kapalı** olduğundan emin olun.

### Teşhis komutu

Cihaz bulunamıyorsa şunu çalıştırıp çıktıyı paylaşın — nedeni netleştirir:

```bash
python main.py --diag
```

Uygulama içindeki "Cihazları Tara" da bu kontrolleri özetleyen bir pencere gösterir.
