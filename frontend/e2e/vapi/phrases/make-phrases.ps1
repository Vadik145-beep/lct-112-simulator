# Озвучка реплик диспетчера голосом Windows (Microsoft Irina, ru-RU) в WAV.
# Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File make-phrases.ps1
# Реплики подобраны так, чтобы попадать в ключевые слова тем caller_topics тренажёра
# (что случилось / адрес / подъезд-этаж-код / пострадавшие / угроза / имя / телефон).

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Irina Desktop")
$s.Rate = 0

$phrases = [ordered]@{
  "01-what-happened" = "Служба сто двенадцать, слушаю вас. Расскажите, что случилось?"
  "02-address"       = "Назовите адрес: улица, дом, корпус."
  "03-entrance"      = "Какой подъезд, этаж и код домофона? Какая квартира?"
  "04-injured"       = "Есть ли пострадавшие? Кому-то плохо, скорая нужна?"
  "05-danger"        = "Запах газа чувствуете? Газ перекрыли, окна открыли?"
  "06-name"          = "Как вас зовут? Представьтесь, пожалуйста."
  "07-phone"         = "Продиктуйте телефон для связи."
  "08-closing"       = "Спасибо. Вызов принят, специалисты Мосгаза выезжают. Ждите, до свидания."
  "09-repeat"        = "Повторите, пожалуйста, я не расслышала адрес. Ещё раз: улица и дом?"
  "10-hello"         = "Алло. Служба сто двенадцать."
  "11-wait"          = "Понятно. Оставайтесь на линии."
}

foreach ($k in $phrases.Keys) {
  $path = Join-Path $here "$k.wav"
  $s.SetOutputToWaveFile($path)
  $s.Speak($phrases[$k])
  $s.SetOutputToNull()
  Write-Host ("{0,-18} {1}" -f $k, $phrases[$k])
}
$s.Dispose()
Remove-Item (Join-Path $here "probe.wav") -ErrorAction SilentlyContinue
