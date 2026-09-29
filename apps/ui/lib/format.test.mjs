import test from 'node:test';
import assert from 'node:assert/strict';
import {formatDate,formatTime,formatRelative,statusLabel,roleLabel,channelLabel,friendlyError,reservationLabel,reservationTone} from './format.mjs';

// 1790000000 is 2026-09-21 14:13:20 UTC, i.e. 19:13 in Tashkent (UTC+5, no DST).
const T=1790000000;

test('one date format: Uzbek month, Tashkent clock',()=>{
  assert.equal(formatDate(T),'21-sentabr 2026, 19:13');
  assert.equal(formatTime(T),'19:13');
  // 23:30 UTC on 31 Dec is already 1 January in Tashkent.
  assert.equal(formatDate(Date.UTC(2025,11,31,23,30)/1000),'1-yanvar 2026, 04:30');
  for(const bad of [NaN,undefined,null,0,-5,'x'])assert.equal(formatDate(bad),'—');
});

test('relative time for lists',()=>{
  assert.equal(formatRelative(T,T+30),'hozirgina');
  assert.equal(formatRelative(T,T-20),'hozirgina');           // small clock skew
  assert.equal(formatRelative(T,T+5*60),'5 daqiqa oldin');
  assert.equal(formatRelative(T,T+59*60),'59 daqiqa oldin');
  assert.equal(formatRelative(T,T+3*3600),'bugun, 19:13');    // 22:13 same day
  assert.equal(formatRelative(T,T+10*3600),'kecha, 19:13');   // 05:13 next day
  assert.equal(formatRelative(T,T+5*86400),'21-sentabr, 19:13');
  assert.equal(formatRelative(T,T+400*86400),'21-sentabr 2026, 19:13');
  assert.equal(formatRelative(T,T-3600),'21-sentabr 2026, 19:13'); // far future: absolute
  assert.equal(formatRelative(NaN,T),'—');
});

test('statuses and roles read as words, unknown codes pass through',()=>{
  assert.equal(statusLabel('succeeded'),'Bajarildi');
  assert.equal(statusLabel('waiting_approval'),'Tasdiq kutmoqda');
  assert.equal(statusLabel('uncertain'),'Natija noma’lum');
  assert.equal(statusLabel('paid'),'To‘langan');
  assert.equal(statusLabel('brand_new'),'brand_new');
  assert.equal(statusLabel(undefined),'');
  assert.equal(roleLabel('owner'),'Egasi');
  assert.equal(roleLabel('viewer'),'Kuzatuvchi');
  assert.equal(roleLabel('x'),'x');
  assert.equal(channelLabel('whatsapp'),'WhatsApp');
  assert.equal(channelLabel('telegram'),'Telegram');
  assert.equal(channelLabel('sms'),'sms');
});

test('errors are explained in Uzbek',()=>{
  assert.match(friendlyError(new TypeError('Failed to fetch')),/Server bilan aloqa yo‘q/);
  assert.match(friendlyError(new TypeError('NetworkError when attempting to fetch resource.')),/aloqa yo‘q/);
  assert.match(friendlyError(new TypeError('Load failed')),/aloqa yo‘q/);
  const status=(n)=>Object.assign(new Error(`API xatosi (${n})`),{status:n});
  assert.match(friendlyError(status(401)),/Sessiya tugagan/);
  assert.match(friendlyError(status(403)),/ruxsat yo‘q/);
  assert.match(friendlyError(status(404)),/topilmadi/);
  assert.match(friendlyError(status(409)),/Holat o‘zgargan/);
  assert.match(friendlyError(status(422)),/noto‘g‘ri/);
  assert.match(friendlyError(status(429)),/Juda ko‘p/);
  assert.match(friendlyError(status(503)),/Serverda/);
  assert.equal(friendlyError(new Error('Butun son kiriting')),'Butun son kiriting');
  assert.equal(friendlyError('nima'),'Amal bajarilmadi. Qayta urinib ko‘ring.');
});

test('budget reservation statuses read as words with a matching tone',()=>{
  assert.equal(reservationLabel('released'),'Qaytarildi');
  assert.equal(reservationLabel('unreconciled'),'Tekshirish kerak');
  assert.equal(reservationLabel('uncertain'),'Natija noma’lum');
  assert.equal(reservationLabel('settled'),'Yopilgan');
  assert.equal(reservationLabel('brand_new'),'brand_new');
  assert.equal(reservationLabel(undefined),'');
  assert.equal(reservationTone('unreconciled'),'warn');
  assert.equal(reservationTone('released'),'ok');
  assert.equal(reservationTone('toString'),'');
  assert.equal(reservationTone('nope'),'');
});
